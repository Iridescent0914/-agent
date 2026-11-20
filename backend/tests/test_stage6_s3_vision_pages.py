from contextlib import contextmanager
from copy import deepcopy
from datetime import timedelta
import hashlib
import json
from uuid import UUID, uuid4
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

from app.core.config import Settings, get_settings
from app.core.database import get_db
from app.main import app
from app.models import Assignment, PaperInstance, PaperInstanceItem, Student, VisionPageCall, VisionPageTask
from app.schemas.vision_pages import CreatePageVisionTasks, PageVisionAction, PageVisionRequest
from app.services import answer_pages, vision_pages as service, vision_page_worker as worker
from app.services.answer_card_artifacts import create_artifact
from app.schemas.answer_pages import CreatePageCollection
from app.services.assessment import AssessmentProblem, now
from app.services.vision_page_protocol import FakePageVisionAdapter, identity_check, parse_page_response
from app.services.assessment_protocol import canonical_json
from tests.test_stage6_s1 import context, protected_counts
from tests.test_stage6_s3_pages import mutation, picture


@pytest.fixture(autouse=True)
def fake_provider(monkeypatch):
    monkeypatch.setattr(get_settings(), "stage6_vision_provider", "fake")


def prepared(context, *, many=False, complete=True):
    db, factory, paper, item, question = context
    if many:
        # Build another paper while draft, then publish; never mutate an existing
        # published item just to force pagination.
        original = item
        assignment = Assignment(title="多页任务fixture", chapter_code="s1")
        db.add(assignment); db.flush()
        paper = PaperInstance(assignment_id=assignment.id, student_id=paper.student_id,
            version_no=1, status="draft", instance_code="VP-" + uuid4().hex)
        db.add(paper); db.flush()
        reference = {"kind": "group", "schema_version": "answer-units-v1", "items": [
            {"item_id": f"sub_{n}.blank_1", "subquestion_no": n, "blank_no": 1,
             "answer": "PRIVATE_REFERENCE", "answer_match_type": "latex_exact"} for n in range(1, 19)]}
        item = PaperInstanceItem(paper_instance_id=paper.id, question_id=question.id, order_no=3,
            question_snapshot=deepcopy(original.question_snapshot), answer_snapshot=reference,
            answer_rule_snapshot=deepcopy(original.answer_rule_snapshot), knowledge_point_snapshot={},
            selection_audit={"source_module": "required"})
        db.add(item); db.flush()
        paper.status, paper.published_at = "published", now()
    artifact, _ = create_artifact(db, paper.id, 1)
    view = answer_pages.create_collection(db, paper.id, artifact.id, CreatePageCollection(
        artifact_code=artifact.artifact_code, instance_code=paper.instance_code, expected_version_no=1))
    if complete:
        for n in range(1, artifact.total_pages + 1):
            view = answer_pages.upload_image(db, paper.id, artifact.id, n, mutation(view["revision_no"]), picture((n * 30 % 255, 20, 20)))
    db.commit()
    return db, factory, paper, artifact, view


def request(view):
    return CreatePageVisionTasks(expected_collection_revision=view["revision_no"],
        expected_input_sha256=view["effective_input_sha256"], reviewer="lin", reason="逐页模拟提取")


def action(task):
    return PageVisionAction(expected_revision_no=task.revision_no, reviewer="lin", reason="核对后处理")


def tasks(db, paper, artifact):
    return service.list_tasks(db, paper.id, artifact.id)


def first_task(db):
    return db.scalar(select(VisionPageTask))


def response_for(task, *, observed=True, readable=True):
    raw = FakePageVisionAdapter().invoke(task.request_payload, image=b"test", image_sha256=hashlib.sha256(b"test").hexdigest(), timeout_seconds=1)
    result = json.loads(raw)
    if observed:
        result["observed_identity"] = deepcopy(task.snapshot["expected_identity"])
    if readable:
        for answer, slot in zip(result["answers"], task.request_payload["slots"]):
            answer.update(recognition_status="readable", answer=slot.get("allowed_option_labels", [r"-\frac{1}{56}"])[0], confidence=.8)
    return result


def test_create_reuse_whitelist_and_no_grading_effects(context):
    db, factory, paper, artifact, view = prepared(context)
    before = protected_counts(db)
    created = service.create_tasks(db, paper.id, artifact.id, request(view))
    assert created["created_count"] == artifact.total_pages == 1
    assert service.create_tasks(db, paper.id, artifact.id, request(view))["created_count"] == 0
    task = first_task(db)
    wire = canonical_json(task.request_payload)
    assert "PRIVATE_" not in wire and paper.instance_code not in wire and artifact.artifact_code not in wire
    assert str(paper.id) not in wire and str(artifact.id) not in wire
    assert set(task.request_payload) == {"schema_version", "request_id", "page_token", "page_size_pt", "slots"}
    assert task.request_payload["schema_version"] == "stage6-vision-request-v2"
    assert all("allowed_option_labels" not in s for s in task.request_payload["slots"])
    assert "answer_snapshot" not in canonical_json(task.snapshot) and "PRIVATE_" not in canonical_json(task.snapshot)
    assert answer_pages.collection_view(db, paper.id, artifact.id)["recognition_status"] == "tasks_created"
    assert not created["can_submit"] and protected_counts(db) == before


def test_incomplete_and_old_collection_revision_cannot_create(context):
    db, factory, paper, artifact, view = prepared(context, complete=False)
    with pytest.raises(AssessmentProblem, match="补齐"):
        service.create_tasks(db, paper.id, artifact.id, request(view))
    after = answer_pages.upload_image(db, paper.id, artifact.id, 1, mutation(), picture())
    with pytest.raises(AssessmentProblem, match="已改变"):
        service.create_tasks(db, paper.id, artifact.id, request(view))
    assert service.create_tasks(db, paper.id, artifact.id, request(after))["created_count"] == 1


def test_unconfigured_real_provider_never_uses_legacy_or_equivalence_fallback(context, monkeypatch):
    db, factory, paper, artifact, view = prepared(context)
    service.create_tasks(db, paper.id, artifact.id, request(view)); db.commit()
    monkeypatch.setattr(get_settings(), "stage6_vision_provider", "bailian")
    monkeypatch.setattr(get_settings(), "vision_provider", "openai_compatible")
    monkeypatch.setattr(get_settings(), "stage6_vision_base_url", "")
    with pytest.raises(AssessmentProblem, match="配置不可用"):
        service.create_tasks(db, paper.id, artifact.id, request(view))
    assert worker.claim_one(factory) is None
    assert tasks(db, paper, artifact)["configuration"]["error_code"] == "VISION_CONFIG_INCOMPLETE"
    assert db.scalar(select(func.count()).select_from(VisionPageCall)) == 0


def test_fake_worker_is_outside_db_and_persists_audit_with_unknown_identity(context):
    db, factory, paper, artifact, view = prepared(context)
    before = protected_counts(db)
    service.create_tasks(db, paper.id, artifact.id, request(view)); db.commit()
    opened = []
    class Factory:
        @contextmanager
        def begin(self):
            with factory.begin() as session:
                opened.append(session)
                try:
                    yield session
                finally:
                    opened.pop()
    class Adapter(FakePageVisionAdapter):
        def invoke(self, *args, **kwargs):
            assert not opened
            return super().invoke(*args, **kwargs)
    assert worker.process_one(Factory(), Adapter())
    db.expire_all()
    task = first_task(db)
    assert task.status == "needs_review" and task.result["synthetic"]
    assert task.result["identity_check"]["status"] == "unconfirmed"
    assert all(a["recognition_status"] == "illegible" and a["answer"] is None for a in task.result["answers"])
    detail = service.task_detail(db, paper.id, artifact.id, task.id)
    assert detail["calls"][0]["raw_response"] and detail["calls"][0]["usage"]["cost_cny"] == 0
    assert detail["calls"][0]["usage"]["synthetic"]
    assert not task.result["can_submit"] and protected_counts(db) == before


def test_simulated_readable_response_with_correct_identity_still_needs_review(context):
    db, factory, paper, artifact, view = prepared(context)
    service.create_tasks(db, paper.id, artifact.id, request(view)); db.commit()
    task = first_task(db)
    parsed = response_for(task)
    class Adapter:
        def invoke(self, *args, **kwargs):
            return canonical_json(parsed)
    assert worker.process_one(factory, Adapter())
    db.expire_all(); task = first_task(db)
    assert task.status == "needs_review" and task.result["identity_check"]["status"] == "verified"
    assert task.result["answers"][0]["answer"] == r"-\frac{1}{56}"
    assert task.result["synthetic"] and task.result["requires_transcription_confirmation"] and not task.result["can_submit"]


@pytest.mark.parametrize("field,bad", [
    ("artifact_code", "WRONG"), ("artifact_revision_no", 99), ("instance_code", "WRONG"),
    ("version_no", 99), ("layout_version", "answer-card-v0"), ("page_no", 2), ("total_pages", 2),
])
def test_wrong_observed_identity_is_blocked_and_raw_preserved(context, field, bad):
    db, factory, paper, artifact, view = prepared(context)
    service.create_tasks(db, paper.id, artifact.id, request(view)); db.commit()
    task = first_task(db)
    parsed = response_for(task)
    parsed["observed_identity"][field] = bad
    if field == "page_no":
        parsed["observed_identity"]["total_pages"] = 2
    raw = canonical_json(parsed)
    class Adapter:
        def invoke(self, *args, **kwargs): return raw
    worker.process_one(factory, Adapter()); db.expire_all()
    task = first_task(db)
    assert task.status == "failed" and task.error_code == "PAGE_IDENTITY_MISMATCH"
    assert task.result is None
    call = db.scalar(select(VisionPageCall))
    assert call.raw_response == raw and call.parsed_response["observed_identity"][field] == bad


def test_retake_invalidates_only_changed_page_and_reuses_others(context):
    db, factory, paper, artifact, view = prepared(context, many=True)
    assert artifact.total_pages > 1
    first = service.create_tasks(db, paper.id, artifact.id, request(view)); db.commit()
    claim = worker.claim_one(factory)
    task = db.get(VisionPageTask, claim.task_id); db.refresh(task)
    changed = task.page_no
    result = response_for(task)
    view = answer_pages.upload_image(db, paper.id, artifact.id, changed, mutation(view["revision_no"]), picture("red")); db.commit()
    worker.finish(claim, canonical_json(result), result, None, factory)
    db.expire_all(); task = db.get(VisionPageTask, claim.task_id)
    assert task.status == "stale" and task.result is None and task.error_code == "PAGE_INPUT_CHANGED"
    assert db.scalar(select(VisionPageCall)).parsed_response is not None
    fresh = service.create_tasks(db, paper.id, artifact.id, request(view))
    assert fresh["created_count"] == 1
    assert len(fresh["current_tasks"]) == artifact.total_pages
    previous_ids = {t["id"] for t in first["current_tasks"] if t["page_no"] != changed}
    assert previous_ids <= {t["id"] for t in fresh["current_tasks"]}
    assert service.task_detail(db, paper.id, artifact.id, task.id)["effective_status"] == "stale"


def test_queued_old_photo_is_never_invoked(context):
    db, factory, paper, artifact, view = prepared(context)
    service.create_tasks(db, paper.id, artifact.id, request(view)); db.commit()
    answer_pages.upload_image(db, paper.id, artifact.id, 1, mutation(view["revision_no"]), picture("red")); db.commit()
    assert worker.claim_one(factory) is None
    db.expire_all(); task = first_task(db)
    assert task.status == "stale" and task.attempt_count == 0
    assert db.scalar(select(func.count()).select_from(VisionPageCall)) == 0


def test_failure_retry_budget_and_explicit_rerun_are_audited(context):
    db, factory, paper, artifact, view = prepared(context)
    service.create_tasks(db, paper.id, artifact.id, request(view)); db.commit()
    class Adapter:
        def invoke(self, *args, **kwargs): raise TimeoutError("PRIVATE_SECRET")
    for n in range(3):
        assert worker.process_one(factory, Adapter())
        db.expire_all(); task = first_task(db)
        assert task.status == "failed" and task.error_code == "MODEL_TIMEOUT" and task.attempt_count == n + 1
        if n < 2:
            service.task_action(db, paper.id, artifact.id, task.id, "retry", action(task)); db.commit()
    with pytest.raises(AssessmentProblem, match="上限"):
        service.task_action(db, paper.id, artifact.id, task.id, "retry", action(task))
    result = service.task_action(db, paper.id, artifact.id, task.id, "rerun", action(task))
    assert result["id"] != str(task.id) and result["generation"] == 2
    assert service.create_tasks(db, paper.id, artifact.id, request(view))["created_count"] == 0
    assert service.task_detail(db, paper.id, artifact.id, task.id)["is_current"] is False
    assert "PRIVATE_SECRET" not in json.dumps(service.task_detail(db, paper.id, artifact.id, task.id), default=str)


def test_lease_recovery_late_and_repeated_finish_do_not_overwrite(context):
    db, factory, paper, artifact, view = prepared(context)
    service.create_tasks(db, paper.id, artifact.id, request(view)); db.commit()
    first = worker.claim_one(factory)
    task = db.get(VisionPageTask, first.task_id); db.refresh(task)
    task.lease_until = now() - timedelta(seconds=1); db.commit()
    second = worker.claim_one(factory)
    assert second.task_id == first.task_id and second.token != first.token
    result = response_for(task)
    worker.finish(first, "old raw", result, None, factory)
    db.expire_all(); task = db.get(VisionPageTask, first.task_id)
    assert task.status == "running" and task.lease_token == second.token and task.result is None
    worker.finish(second, "new raw", result, None, factory)
    worker.finish(first, "overwrite old", result, None, factory)
    worker.finish(second, "overwrite new", result, None, factory)
    db.expire_all()
    calls = db.scalars(select(VisionPageCall).order_by(VisionPageCall.call_no)).all()
    assert [(c.status, c.raw_response) for c in calls] == [("late", "old raw"), ("succeeded", "new raw")]
    assert db.get(VisionPageTask, first.task_id).status == "needs_review"


def test_cancel_and_stale_action_revision_are_safe(context):
    db, factory, paper, artifact, view = prepared(context)
    service.create_tasks(db, paper.id, artifact.id, request(view)); db.commit()
    task = first_task(db); previous_action = action(task)
    claim = worker.claim_one(factory); db.refresh(task)
    with pytest.raises(AssessmentProblem, match="已改变"):
        service.task_action(db, paper.id, artifact.id, task.id, "cancel", previous_action)
    service.task_action(db, paper.id, artifact.id, task.id, "cancel", action(task)); db.commit()
    result = response_for(task)
    worker.finish(claim, canonical_json(result), result, None, factory)
    db.expire_all(); task = first_task(db)
    assert task.status == "cancelled" and task.result is None
    assert db.scalar(select(VisionPageCall)).late_response
    service.task_action(db, paper.id, artifact.id, task.id, "retry", action(task)); db.commit()
    assert worker.process_one(factory)


def test_expiry_and_withdrawal_make_results_unusable_even_before_worker_runs(context):
    db, factory, paper, artifact, view = prepared(context)
    service.create_tasks(db, paper.id, artifact.id, request(view)); db.commit()
    task = first_task(db)
    from app.models import AnswerPageImage
    image = db.get(AnswerPageImage, task.image_id)
    image.retention_until = now() - timedelta(seconds=1); db.flush()
    assert tasks(db, paper, artifact)["current_tasks"] == []
    with pytest.raises(AssessmentProblem, match="已变化"):
        service.task_action(db, paper.id, artifact.id, task.id, "rerun", action(task))
    paper.invalidated_at = now(); db.flush()
    assert service.task_detail(db, paper.id, artifact.id, task.id)["binding_error"] == "INPUT_STALE"


def test_restoring_same_photo_after_retake_does_not_revive_old_lease(context):
    db, factory, paper, artifact, view = prepared(context)
    service.create_tasks(db, paper.id, artifact.id, request(view)); db.commit()
    old = first_task(db)
    claim = worker.claim_one(factory)
    red = answer_pages.upload_image(db, paper.id, artifact.id, 1, mutation(view["revision_no"]), picture("red"))
    answer_pages.select_image(db, paper.id, artifact.id, 1, mutation(red["revision_no"], old.image_id)); db.commit()
    parsed = response_for(old)
    worker.finish(claim, canonical_json(parsed), parsed, None, factory)
    db.expire_all(); old = db.get(VisionPageTask, old.id)
    assert old.status == "stale" and old.result is None
    current = answer_pages.collection_view(db, paper.id, artifact.id)
    assert service.create_tasks(db, paper.id, artifact.id, request(current))["created_count"] == 1


@pytest.mark.parametrize("mode,expected", [
    ("json", "MODEL_SCHEMA_INVALID"), ("oversized", "MODEL_RESPONSE_TOO_LARGE"),
    ("provider", "MODEL_PROVIDER_FAILED"), ("non_string", "MODEL_SCHEMA_INVALID"),
])
def test_bad_adapter_outputs_keep_safe_audit_and_cannot_be_results(context, mode, expected):
    db, factory, paper, artifact, view = prepared(context)
    service.create_tasks(db, paper.id, artifact.id, request(view)); db.commit()
    class Adapter:
        def invoke(self, *args, **kwargs):
            if mode == "provider": raise RuntimeError("PRIVATE_SECRET")
            if mode == "json": return '{"wrong":1}'
            if mode == "oversized": return "x" * 300000
            return {"not": "a string"}
    assert worker.process_one(factory, Adapter())
    db.expire_all(); task = first_task(db)
    assert task.status == "failed" and task.error_code == expected and task.result is None
    detail = service.task_detail(db, paper.id, artifact.id, task.id)
    assert "PRIVATE_SECRET" not in json.dumps(detail, default=str)
    if mode == "json": assert detail["calls"][0]["raw_response"] == '{"wrong":1}'
    if mode == "oversized":
        assert len(detail["calls"][0]["raw_response"]) == 262144
        assert detail["calls"][0]["usage"]["raw_response_truncated"]


def test_provider_return_after_deadline_is_not_accepted(context, monkeypatch):
    db, factory, paper, artifact, view = prepared(context)
    service.create_tasks(db, paper.id, artifact.id, request(view)); db.commit()
    ticks = iter([0., 999.])
    monkeypatch.setattr(worker, "time", SimpleNamespace(monotonic=lambda: next(ticks)))
    assert worker.process_one(factory)
    db.expire_all()
    assert first_task(db).error_code == "MODEL_TIMEOUT" and first_task(db).result is None


def test_simulated_task_limits_and_corrupt_image_block_calls(context, monkeypatch):
    db, factory, paper, artifact, view = prepared(context)
    monkeypatch.setattr(service, "MAX_TASKS", 0)
    with pytest.raises(AssessmentProblem, match="上限"):
        service.create_tasks(db, paper.id, artifact.id, request(view))
    monkeypatch.setattr(service, "MAX_TASKS", 1000)
    service.create_tasks(db, paper.id, artifact.id, request(view)); db.commit()
    task = first_task(db)
    from app.models import AnswerPageImage
    image = db.get(AnswerPageImage, task.image_id)
    image.image_data = b"corrupt"; db.commit()
    assert worker.claim_one(factory) is None
    db.expire_all()
    assert first_task(db).error_code == "IMAGE_INTEGRITY_ERROR"
    assert db.scalar(select(func.count()).select_from(VisionPageCall)) == 0


def test_task_snapshot_cannot_be_changed_or_deleted_by_sql(context):
    db, factory, paper, artifact, view = prepared(context)
    service.create_tasks(db, paper.id, artifact.id, request(view)); db.commit()
    for sql in ["UPDATE vision_page_tasks SET snapshot='{}'::jsonb", "DELETE FROM vision_page_tasks"]:
        with pytest.raises(DBAPIError, match="immutable"):
            with db.begin_nested(): db.execute(text(sql))


def test_api_scoped_create_detail_and_task_actions_never_grade(context):
    db, factory, paper, artifact, view = prepared(context)
    before = protected_counts(db)
    app.dependency_overrides[get_db] = lambda: db
    path = f"/api/v1/internal/stage6/paper-instances/{paper.id}/answer-card-artifacts/{artifact.id}/page-collection/vision-tasks"
    try:
        client = TestClient(app)
        result = client.post(path, json=request(view).model_dump(mode="json"))
        assert result.status_code == 200, result.text
        task = first_task(db)
        assert client.get(path).json()["current_tasks"][0]["id"] == str(task.id)
        assert client.get(path + f"/{task.id}").json()["snapshot"]["artifact_id"] == str(artifact.id)
        assert client.get(path.replace(str(paper.id), str(uuid4())) + f"/{task.id}").status_code == 404
        assert client.post(path + f"/{task.id}/cancel", json=action(task).model_dump(mode="json")).status_code == 200
        assert protected_counts(db) == before
    finally:
        app.dependency_overrides.pop(get_db, None)


def protocol_fixture():
    request = {"schema_version": "stage6-vision-request-v2", "request_id": str(uuid4()), "page_token": "opaque",
        "page_size_pt": [595.276, 841.89], "slots": [
            {"slot_token": "one", "order_no": 3, "subquestion_no": 1, "blank_no": 1,
             "answer_format": "choice_letter", "allowed_option_labels": ["A", "B", "E"], "writing_bbox_pt": [100., 200., 200., 250.]},
            {"slot_token": "two", "order_no": 3, "subquestion_no": 2, "blank_no": 1,
             "answer_format": "math_or_text", "writing_bbox_pt": [100., 300., 200., 350.]},
        ]}
    raw = FakePageVisionAdapter().invoke(request, image=b"x", image_sha256=hashlib.sha256(b"x").hexdigest(), timeout_seconds=1)
    return request, json.loads(raw)


@pytest.mark.parametrize("mutation", [
    lambda r: r.update(schema_version="stage6-vision-response-v1"),
    lambda r: r.update(request_id=str(uuid4())), lambda r: r.update(page_token="other"),
    lambda r: r.update(extra="invented"), lambda r: r["observed_identity"].pop("artifact_code"),
    lambda r: r["observed_identity"].update(version_no=True),
    lambda r: r["observed_identity"].update(page_no=2, total_pages=1),
    lambda r: r["answers"].pop(), lambda r: r["answers"].append(deepcopy(r["answers"][0])),
    lambda r: r["answers"][0].update(slot_token="foreign"),
    lambda r: r["answers"][0].update(recognition_status="readable", answer="D"),
    lambda r: r["answers"][0].update(recognition_status="readable", answer=""),
    lambda r: r["answers"][0].update(recognition_status="blank", answer="A"),
    lambda r: r["answers"][0].update(confidence=float('inf')),
])
def test_strict_v2_response_rejects_invalid_or_misaligned_payloads(mutation):
    request, response = protocol_fixture(); mutation(response)
    with pytest.raises(ValueError): parse_page_response(json.dumps(response), request)


def test_v2_duplicate_keys_fraction_fidelity_and_empty_template_page():
    request, response = protocol_fixture()
    response["answers"][1].update(recognition_status="readable", answer=r"-\frac{1}{56}")
    parsed = parse_page_response(canonical_json(response), request)
    assert parsed["answers"][1]["answer"] == r"-\frac{1}{56}"
    raw = canonical_json(response)
    with pytest.raises(ValueError, match="duplicate"):
        parse_page_response(raw.replace('"confidence":null', '"confidence":null,"confidence":null', 1), request)
    request["slots"] = []; response["answers"] = []
    assert parse_page_response(canonical_json(response), request)["answers"] == []


@pytest.mark.parametrize("change", [
    lambda r: r["slots"][0].update(writing_bbox_pt=[100., 100., 50., 150.]),
    lambda r: r["slots"][0].update(writing_bbox_pt=[100., 100., 800., 150.]),
    lambda r: r["slots"][0].update(writing_bbox_pt=[100., 100., float('nan'), 150.]),
    lambda r: r.update(page_size_pt=[float('inf'), 841.89]),
    lambda r: r["slots"][1].update(allowed_option_labels=None),
    lambda r: r["slots"].append(deepcopy(r["slots"][0])),
])
def test_v2_request_rejects_bad_geometry_options_and_duplicates(change):
    request, response = protocol_fixture(); change(request)
    with pytest.raises(ValueError): PageVisionRequest.model_validate(request)


def test_gate_lease_configuration_and_fake_image_hash_check(context, monkeypatch):
    db, factory, paper, artifact, view = prepared(context)
    monkeypatch.setattr(get_settings(), "stage6_enabled", False)
    assert worker.claim_one(factory) is None
    with pytest.raises(AssessmentProblem) as error: tasks(db, paper, artifact)
    assert error.value.status_code == 403
    monkeypatch.setattr(get_settings(), "stage6_enabled", True)
    monkeypatch.setattr(get_settings(), "app_env", "production")
    assert worker.claim_one(factory) is None
    with pytest.raises(ValueError):
        Settings(stage6_vision_timeout_seconds=45, stage6_vision_lease_seconds=45)
    request_value, _ = protocol_fixture()
    with pytest.raises(ValueError, match="INTEGRITY"):
        FakePageVisionAdapter().invoke(request_value, image=b"x", image_sha256="0" * 64, timeout_seconds=1)
