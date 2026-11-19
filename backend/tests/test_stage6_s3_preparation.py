from copy import deepcopy
import json
from types import SimpleNamespace
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app.schemas.vision_preparation import (
    IdentityConfirmation, PageAssignment, TrialPageResult, VisionTrialPlan,
)
from app.services.assessment_protocol import FakeVisionAdapter, input_hash
from app.services.vision_preparation import (
    build_inventory, build_trial_plan, inventory_from_snapshots, merge_trial_results,
)


def snapshots():
    paper = SimpleNamespace(id=uuid4(), status="published", instance_code="RAT-7K3M-9Q2H",
                            version_no=1, invalidated_at=None)
    items = []
    for number, kind, module, parts in [
        (1, "choice", "required", ["sub_1.blank_1"]),
        (2, "fill_blank", "history", ["sub_1.blank_1", "sub_1.blank_2"]),
        (3, "true_false", "personalized", ["sub_1.blank_1"]),
    ]:
        items.append(SimpleNamespace(
            id=uuid4(), paper_instance_id=paper.id, order_no=number,
            question_snapshot={"question_type": kind, "question_text": "PRIVATE_STEM",
                               "solution": "PRIVATE_SOLUTION", "student_name": "PRIVATE_NAME"},
            answer_snapshot={"kind": "group", "items": [
                {"item_id": item_id, "answer": "PRIVATE_ANSWER", "accepted_answers": ["PRIVATE_ALIAS"]}
                for item_id in parts]},
            options_snapshot={"A": "PRIVATE_OPTION", "C": "PRIVATE_OPTION", "E": "PRIVATE_OPTION"}
            if kind == "choice" else None,
            selection_audit={"source_module": module},
        ))
    return paper, items


def plan_fixture():
    paper, items = snapshots()
    inventory = inventory_from_snapshots(paper, items)
    keys = [slot.slot_key for slot in inventory.slots]
    # Physical page order differs from paper order: required, personalized,
    # history, including a question whose blanks appear on different pages.
    plan = build_trial_plan(inventory, [
        PageAssignment(page_no=1, slot_keys=[keys[0], keys[3], keys[1]]),
        PageAssignment(page_no=2, slot_keys=[keys[2]]),
    ])
    return inventory, plan


def output(plan, number, *, status="readable", observed=True):
    page = next(p for p in plan.pages if p.page_no == number)
    answers = []
    for slot in page.request.slots:
        answer = ("E" if slot.answer_format == "choice_letter" else "-1/56") if status == "readable" else None
        answers.append({"slot_token": slot.slot_token, "recognition_status": status,
                        "answer": answer, "confidence": .99})
    identity = {"instance_code": plan.inventory.instance_code, "version_no": 1,
                "page_no": number, "total_pages": len(plan.pages)}
    payload = {"schema_version": "stage6-vision-response-v1", "request_id": page.request.request_id,
               "page_token": page.request.page_token,
               "observed_identity": identity if observed else {key: None for key in identity},
               "answers": answers}
    return TrialPageResult(page_no=number, image_sha256=input_hash({"page": number}),
                           raw_response=json.dumps(payload))


def modify(result, mutate):
    payload = json.loads(result.raw_response)
    mutate(payload)
    return result.model_copy(update={"raw_response": json.dumps(payload)})


def test_inventory_uses_only_frozen_coordinates_and_is_read_only():
    paper, items = snapshots()
    original = deepcopy([vars(item) for item in items])
    inventory = inventory_from_snapshots(paper, reversed(items))
    assert "PRIVATE_" not in inventory.model_dump_json()
    assert inventory.formal_recognition_ready is False
    assert [(s.order_no, s.subquestion_no, s.blank_no) for s in inventory.slots] == [
        (1, 1, 1), (2, 1, 1), (2, 1, 2), (3, 1, 1)]
    assert [s.source_module for s in inventory.slots] == ["required", "history", "history", "personalized"]
    assert inventory.slots[0].allowed_option_labels == ["A", "C", "E"]
    assert inventory == inventory_from_snapshots(paper, items)
    assert original == [vars(item) for item in items]

    class ReadOnlyDB:
        def get(self, model, identity):
            assert identity == paper.id
            return paper

        def scalars(self, statement):
            return items

    assert build_inventory(ReadOnlyDB(), paper.id) == inventory


@pytest.mark.parametrize("change", [
    lambda p, items: setattr(p, "status", "draft"),
    lambda p, items: setattr(p, "invalidated_at", "withdrawn"),
    lambda p, items: setattr(items[0], "paper_instance_id", uuid4()),
    lambda p, items: setattr(items[1], "order_no", 1),
    lambda p, items: items[1].answer_snapshot["items"].append(items[1].answer_snapshot["items"][0]),
])
def test_invalid_or_obsolete_paper_is_rejected(change):
    paper, items = snapshots()
    change(paper, items)
    with pytest.raises(ValueError):
        inventory_from_snapshots(paper, items)


def test_missing_choice_labels_and_manual_units_are_explicit_blockers():
    paper, items = snapshots()
    items[0].options_snapshot = None
    items[1].answer_snapshot["items"][0]["answer_match_type"] = "manual"
    inventory = inventory_from_snapshots(paper, items)
    assert [s.blocker for s in inventory.slots] == ["CHOICE_LABELS_MISSING", "MANUAL_SLOT", None, None]
    with pytest.raises(ValueError, match="不可静默跳过"):
        build_trial_plan(inventory, [PageAssignment(page_no=1, slot_keys=[s.slot_key for s in inventory.slots])])


def test_page_requests_are_whitelisted_opaque_and_keep_actual_choice_alphabet():
    inventory, plan = plan_fixture()
    wire = plan.pages[0].provider_payload()
    assert set(wire) == {"schema_version", "request_id", "page_token", "slots"}
    serialized = json.dumps(wire)
    for forbidden in [inventory.paper_instance_id, inventory.instance_code, "PRIVATE_", "slot_key",
                      "source_module", "item_id", "student", "reference", "rule"]:
        assert forbidden not in serialized
    assert wire["slots"][0]["allowed_option_labels"] == ["A", "C", "E"]
    assert all("allowed_option_labels" not in slot for slot in wire["slots"][1:])
    assert len({p.request.page_token for p in plan.pages}) == 2
    inventory.slots[0].blank_no = 9
    assert plan.inventory.slots[0].blank_no == 1
    # Normal JSON exports preserve omitted optional fields in the wire protocol.
    restored = VisionTrialPlan.model_validate_json(plan.model_dump_json())
    assert merge_trial_results(restored, [output(restored, 1), output(restored, 2)])["capture_status"] == "ready"


@pytest.mark.parametrize("variant", ["missing", "duplicate", "extra", "gap", "duplicate_page"])
def test_page_assignment_requires_complete_disjoint_coverage(variant):
    inventory, plan = plan_fixture()
    keys = [s.slot_key for s in inventory.slots]
    assignments = [PageAssignment(page_no=1, slot_keys=keys[:2]), PageAssignment(page_no=2, slot_keys=keys[2:])]
    if variant == "missing":
        assignments[1].slot_keys.pop()
    elif variant == "duplicate":
        assignments[1].slot_keys.append(keys[0])
    elif variant == "extra":
        assignments[1].slot_keys.append("unknown")
    elif variant == "gap":
        assignments[1].page_no = 3
    else:
        assignments[1].page_no = 1
    with pytest.raises(ValueError):
        build_trial_plan(inventory, assignments)


def test_merge_preserves_fraction_and_paper_order_without_grading():
    _, plan = plan_fixture()
    merged = merge_trial_results(plan, [output(plan, 2), output(plan, 1)])
    assert merged["capture_status"] == "ready"
    assert merged["can_submit"] is False
    assert merged["requires_transcription_confirmation"] is True
    assert merged["received_unit_count"] == merged["expected_unit_count"] == 4
    assert [u["order_no"] for u in merged["units"]] == [1, 2, 2, 3]
    assert [u["page_no"] for u in merged["units"]] == [1, 1, 2, 1]
    assert merged["units"][1]["answer"] == "-1/56"
    assert all("is_correct" not in unit and "grade_status" not in unit for unit in merged["units"])


def test_absent_page_is_collecting_and_does_not_create_blank_answers():
    _, plan = plan_fixture()
    result = merge_trial_results(plan, [output(plan, 1)])
    assert result["capture_status"] == "collecting"
    assert result["missing_pages"] == [2]
    assert result["received_unit_count"] == 3
    assert not any(u["recognition_status"] == "blank" for u in result["units"])


def test_single_page_plan_and_unexpected_page_do_not_depend_on_upload_order():
    inventory, _ = plan_fixture()
    plan = build_trial_plan(inventory, [PageAssignment(page_no=1, slot_keys=[s.slot_key for s in inventory.slots])])
    first = output(plan, 1)
    assert merge_trial_results(plan, [first])["capture_status"] == "ready"
    unexpected = first.model_copy(update={"page_no": 2, "image_sha256": input_hash("unexpected")})
    result = merge_trial_results(plan, [unexpected, first])
    assert result["capture_status"] == "needs_review"
    assert result["missing_pages"] == []
    assert result["received_unit_count"] == 4
    assert result["issues"] == [{"page_no": 2, "code": "UNEXPECTED_PAGE"}]


@pytest.mark.parametrize("status", ["blank", "missing", "illegible", "ambiguous"])
def test_blank_is_distinct_from_missing_or_unreadable(status):
    _, plan = plan_fixture()
    result = merge_trial_results(plan, [output(plan, 1), output(plan, 2, status=status)])
    assert result["missing_pages"] == []
    assert result["capture_status"] == ("ready" if status == "blank" else "needs_review")
    assert result["units"][2]["recognition_status"] == status
    assert result["units"][2]["answer"] is None


@pytest.mark.parametrize("mutation", [
    lambda p: p["observed_identity"].update(instance_code="WRONG"),
    lambda p: p["observed_identity"].update(version_no=2),
    lambda p: p["observed_identity"].update(page_no=2),
    lambda p: p["observed_identity"].update(total_pages=3),
])
def test_wrong_identity_cannot_be_overridden_by_manual_confirmation(mutation):
    _, plan = plan_fixture()
    result = modify(output(plan, 1), mutation)
    result.identity_confirmation = IdentityConfirmation(reviewer="teacher", reason="already checked")
    merged = merge_trial_results(plan, [result, output(plan, 2)])
    assert merged["capture_status"] == "needs_review"
    assert merged["issues"][0]["code"] == "PAGE_IDENTITY_MISMATCH"
    assert merged["received_unit_count"] == 1


def test_unreadable_identity_needs_explicit_auditable_confirmation():
    _, plan = plan_fixture()
    first, second = output(plan, 1, observed=False), output(plan, 2)
    merged = merge_trial_results(plan, [first, second])
    assert merged["issues"][0]["code"] == "PAGE_IDENTITY_UNCONFIRMED"
    first.identity_confirmation = IdentityConfirmation(reviewer="teacher", reason="对照打印原件核对页归属")
    merged = merge_trial_results(plan, [first, second])
    assert merged["capture_status"] == "ready"
    assert merged["identity_confirmations"][0]["reviewer"] == "teacher"
    assert len(merged["identity_confirmations"][0]["fields"]) == 4


@pytest.mark.parametrize("mutation", [
    lambda p: p.update(request_id=str(uuid4())),
    lambda p: p.update(page_token="obsolete"),
    lambda p: p["answers"].pop(),
    lambda p: p["answers"].append(p["answers"][0]),
    lambda p: p["answers"][0].update(slot_token="foreign"),
    lambda p: p["answers"][0].update(answer="B"),  # Actual alphabet is A,C,E.
    lambda p: p["answers"][0].update(recognition_status="blank"),
])
def test_stale_malformed_or_incomplete_page_is_not_merged(mutation):
    _, plan = plan_fixture()
    merged = merge_trial_results(plan, [modify(output(plan, 1), mutation), output(plan, 2)])
    assert merged["issues"][0]["code"] == "INVALID_PAGE_RESPONSE"
    assert merged["received_unit_count"] == 1


def test_duplicate_json_keys_and_duplicate_page_or_image_are_blocked():
    _, plan = plan_fixture()
    first, second = output(plan, 1), output(plan, 2)
    bad = first.model_copy(update={"raw_response": first.raw_response[:-1] + ',"answers":[]}'})
    assert merge_trial_results(plan, [bad, second])["issues"][0]["code"] == "INVALID_PAGE_RESPONSE"
    assert merge_trial_results(plan, [first, first, second])["received_unit_count"] == 1
    second.image_sha256 = first.image_sha256
    result = merge_trial_results(plan, [first, second])
    assert result["received_unit_count"] == 0
    assert {i["code"] for i in result["issues"]} == {"DUPLICATE_IMAGE"}


def test_fake_cannot_claim_real_transcription_success():
    _, plan = plan_fixture()
    results = []
    for page in plan.pages:
        raw = FakeVisionAdapter().invoke(page.provider_payload(), timeout_seconds=1)
        results.append(TrialPageResult(page_no=page.page_no, image_sha256=input_hash({"page": page.page_no}),
                                       raw_response=raw, identity_confirmation=IdentityConfirmation(
                                           reviewer="developer", reason="offline fixture only")))
    merged = merge_trial_results(plan, results)
    assert merged["capture_status"] == "needs_review"
    assert all(unit["recognition_status"] == "illegible" for unit in merged["units"])
    assert merged["can_submit"] is False


def test_changed_inventory_or_request_mapping_invalidates_old_plan():
    inventory, plan = plan_fixture()
    inventory.slots[0].blank_no = 2
    with pytest.raises(ValueError, match="已变更"):
        build_trial_plan(inventory, [PageAssignment(page_no=1, slot_keys=[s.slot_key for s in inventory.slots])])
    plan.pages[0].request.slots[0].blank_no = 8
    with pytest.raises(ValueError, match="规格"):
        merge_trial_results(plan, [])


def test_result_from_a_rebuilt_page_plan_cannot_be_reused_as_current():
    inventory, old_plan = plan_fixture()
    old_results = [output(old_plan, 1), output(old_plan, 2)]
    new_plan = build_trial_plan(inventory, [
        PageAssignment(page_no=page.page_no, slot_keys=list(page.slot_bindings.values()))
        for page in old_plan.pages
    ])
    result = merge_trial_results(new_plan, old_results)
    assert result["received_unit_count"] == 0
    assert {issue["code"] for issue in result["issues"]} == {"INVALID_PAGE_RESPONSE"}


def test_read_only_api_is_internal_and_disabled_by_default(monkeypatch):
    from app.api.routes import assessment as routes
    from app.core.database import get_db
    from app.services.assessment import AssessmentProblem

    inventory, _ = plan_fixture()
    settings = SimpleNamespace(app_env="test", stage6_enabled=True)
    monkeypatch.setattr(routes, "get_settings", lambda: settings)
    monkeypatch.setattr(routes.service, "get_settings", lambda: settings)
    monkeypatch.setattr(routes, "build_inventory", lambda db, paper_id: inventory)
    api = FastAPI()
    api.include_router(routes.router)
    api.dependency_overrides[get_db] = lambda: object()
    # A tiny app avoids startup workers and any database/network interaction.
    @api.exception_handler(AssessmentProblem)
    async def problem_handler(request, exc):
        from fastapi.responses import JSONResponse
        return JSONResponse({"code": exc.code}, status_code=exc.status_code)

    with TestClient(api) as client:
        url = f"/internal/stage6/paper-instances/{inventory.paper_instance_id}/vision-preparation"
        assert client.get(url).json() == inventory.model_dump()
        settings.stage6_enabled = False
        assert client.get(url).status_code == 403
        settings.stage6_enabled, settings.app_env = True, "production"
        assert client.get(url).status_code == 403
