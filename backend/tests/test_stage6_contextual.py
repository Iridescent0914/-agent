"""Context selection and protocol guarantees; no claim of live-model quality."""
from copy import deepcopy
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select

from app.models import AssessmentTask
from app.schemas.assessment import ContextualEquivalenceRequest, ContextualEquivalenceResponse
from app.services import assessment, paper_equivalence
from app.services.answer_context import build_context, ContextUnavailable
from app.services.assessment_protocol import FakeEquivalenceAdapter, canonical_json, parse_response
from app.services.equivalence_bailian import payload_for
from tests.test_stage6_s5 import context, base_context, submit, audit_request, complete, adoption


def item(text, assets=None):
    return SimpleNamespace(question_snapshot={"question_text": text, "assets": assets or [],
        "solution": "PRIVATE_SOLUTION", "wrong_analysis": "PRIVATE_ANALYSIS", "student_name": "PRIVATE_IDENTITY"})


def test_context_keeps_shared_conditions_and_marks_only_target_without_other_answers():
    q = item("已知两个非零有理数。\n（1）绝对值________。\n（2）取绝对值________的数的符号，另一结果________。")
    coords = {"a": (1, 1), "b": (2, 1), "c": (2, 2)}
    binding = build_context(q, coords, "b")
    value = binding["answer_context"]
    assert value["scope"] == "subquestion"
    assert value["question_text"] == "已知两个非零有理数。\n（2）取绝对值【本空】的数的符号，另一结果________。"
    assert value["target_subquestion_no"] == 2 and value["target_blank_no"] == 1
    assert "PRIVATE_" not in canonical_json(binding)
    assert set(value) == {"scope", "question_text", "target_subquestion_no", "target_blank_no"}
    assert build_context(q, coords, "c")["context_sha256"] != binding["context_sha256"]


def test_cross_part_or_global_instructions_keep_full_question_group():
    text = "（1）填写________。\n（2）根据第1小题填写________。\n注意：所有结果保留两位小数。"
    value = build_context(item(text), {"a": (1, 1), "b": (2, 1)}, "b")["answer_context"]
    assert value["scope"] == "question_group"
    assert "（1）填写________" in value["question_text"]
    assert "保留两位小数" in value["question_text"]
    assert "（2）根据第1小题填写【本空】" in value["question_text"]


def test_text_latex_is_preserved_without_reading_formula_assets():
    value = build_context(item(r"化简 $\frac{x+x}{2}$ 得________。"), {"answer": (1, 1)}, "answer")["answer_context"]
    assert r"\frac{x+x}{2}" in value["question_text"] and "【本空】" in value["question_text"]


def test_single_answer_without_underline_has_unique_response_location():
    value = build_context(item("化简x+x。"), {"answer": (1, 1)}, "answer")["answer_context"]
    assert value["question_text"] == "化简x+x。\n本题唯一作答位置：【本空】"
    assert value["scope"] == "question_group"


def test_inline_numbered_parts_use_full_group_instead_of_risky_cropping():
    text = "材料：（1）填________。（2）填________。"
    value = build_context(item(text), {"a": (1, 1), "b": (2, 1)}, "b")["answer_context"]
    assert value["scope"] == "question_group" and "（1）填________" in value["question_text"]
    assert "（2）填【本空】" in value["question_text"]


@pytest.mark.parametrize("text,assets,coords,reason", [
    ("", [], {"a": (1, 1)}, "context_missing"),
    ("（1）填写________。\n（3）填写________。", [], {"a": (1, 1), "b": (2, 1)}, "context_ambiguous"),
    ("填写________和________。", [], {"a": (1, 1)}, "context_ambiguous"),
    ("填写________。", [], {"a": (1, 2)}, "context_ambiguous"),
    ("[ASSET:assets/figure.svg]填写________。", [{"path": "assets/figure.svg"}], {"a": (1, 1)}, "context_incomplete"),
    ("填写________。", [{"path": "assets/figure.svg"}], {"a": (1, 1)}, "context_incomplete"),
    ("如图，AB是________的边。", [], {"a": (1, 1)}, "context_incomplete"),
    (r"数值为\placeholder{}，填写________。", [], {"a": (1, 1)}, "context_incomplete"),
    ("已有【本空】并填写________。", [], {"a": (1, 1)}, "context_ambiguous"),
    ("条件" * 6100 + "________", [], {"a": (1, 1)}, "context_too_long"),
])
def test_incomplete_or_ambiguous_context_blocks_instead_of_guessing(text, assets, coords, reason):
    with pytest.raises(ContextUnavailable) as exc:
        build_context(item(text, assets), coords, "a")
    assert exc.value.code == reason


def test_assets_in_unrelated_part_do_not_disable_text_only_context():
    q = item("（1）填写________。\n（2）[ASSET:assets/figure.svg]填写________。", [{"path": "assets/figure.svg"}])
    binding = build_context(q, {"a": (1, 1), "b": (2, 1)}, "a")
    assert "ASSET" not in binding["answer_context"]["question_text"]


def test_task_uses_published_stem_and_freezes_context_not_current_question(context):
    db, _, paper, question_item, question = context
    submit(context)
    report = paper_equivalence.preview(db, paper.id)
    assert all(u.get("answer_context") for u in report["units"] if u["eligible"])
    paper_equivalence.schedule(db, paper.id, audit_request(report))
    tasks = list(db.scalars(select(AssessmentTask)))
    frozen = deepcopy(tasks[0].request_payload)
    question.question_text = "PRIVATE_CURRENT_QUESTION_CHANGED"; db.flush()
    assert assessment.binding_error(db, tasks[0]) is None
    assert tasks[0].request_payload == frozen and "PRIVATE_" not in canonical_json(frozen)
    assert frozen["schema_version"] == "stage6-equivalence-request-v2"
    assert frozen["answer_context"]["question_text"].count("【本空】") == 1
    tasks[0].request_payload = {**frozen, "answer_context": {**frozen["answer_context"], "target_blank_no": 99}}
    db.flush()
    assert assessment.binding_error(db, tasks[0]) == "INPUT_STALE"


def test_context_failure_cannot_be_bypassed_by_direct_create_or_fallback_to_pair_only(context, monkeypatch):
    db, _, paper, question_item, _ = context
    submit(context)
    def unavailable(*args):
        raise ContextUnavailable("context_incomplete", "未文本化的题图，请人工复核")
    monkeypatch.setattr(assessment, "build_context", unavailable)
    preview = assessment.create_task(db, assessment.CreateAssessmentTask(paper_instance_item_id=question_item.id,
        item_id="sub_1.blank_1", expected_attempt_revision=1), preview_only=True)
    assert not preview["eligible"] and preview["reason_code"] == "context_incomplete"
    with pytest.raises(assessment.AssessmentProblem) as exc:
        assessment.create_task(db, assessment.CreateAssessmentTask(paper_instance_item_id=question_item.id,
            item_id="sub_1.blank_1", expected_attempt_revision=1))
    assert exc.value.code == "NOT_ELIGIBLE"
    assert db.scalar(select(func.count()).select_from(AssessmentTask)) == 0


def test_legacy_tasks_remain_readable_but_are_not_reused_or_adopted(context):
    db, _, paper, question_item, _ = context
    submit(context)
    report = paper_equivalence.preview(db, paper.id)
    paper_equivalence.schedule(db, paper.id, audit_request(report))
    tasks = complete(db, paper)
    for task in tasks:
        task.policy_version = "stage6-equivalence-candidates-v2"
        task.request_payload = {k: v for k, v in task.request_payload.items() if k != "answer_context"}
        task.request_payload = {**task.request_payload, "schema_version": "stage6-equivalence-request-v1"}
        task.result = {**task.result, "schema_version": "stage6-equivalence-response-v1", "reason_code": "equivalent_form"}
    db.flush()
    original = deepcopy(tasks[0].result)
    report = paper_equivalence.preview(db, paper.id)
    assert report["new_task_count"] == 2
    assert assessment.get_task(db, tasks[0].id).status == "stale"
    assert assessment.task_view(db, tasks[0])["result"] == original
    with pytest.raises(assessment.AssessmentProblem):
        paper_equivalence.adopt(db, paper.id, adoption(report, tasks))


@pytest.mark.parametrize("reason", ["reference_issue", "context_incomplete"])
def test_new_uncertainty_reasons_are_valid_and_cannot_update_grading(context, reason):
    db, _, paper, _, _ = context
    submit(context)
    report = paper_equivalence.preview(db, paper.id)
    paper_equivalence.schedule(db, paper.id, audit_request(report))
    tasks = complete(db, paper, verdict="uncertain")
    tasks[0].result = {**tasks[0].result, "reason_code": reason}; db.flush()
    ContextualEquivalenceResponse.model_validate(tasks[0].result)
    report = paper_equivalence.preview(db, paper.id)
    unit = next(u for u in report["units"] if u.get("task", {}) and u["task"]["id"] == str(tasks[0].id))
    assert not unit["task"]["adoptable"] and unit["task"]["adoption_blocker"]["code"] == reason


def test_v2_transport_is_strict_and_v1_remains_an_explicit_reading_format():
    wire = {"schema_version": "stage6-equivalence-request-v2", "request_id": str(uuid4()),
        "reference_answer": "较大", "student_answer": "大", "answer_context": {
            "scope": "question_group", "question_text": "选出两个数中【本空】的一个。", "target_subquestion_no": 1, "target_blank_no": 1}}
    result = json.loads(FakeEquivalenceAdapter().invoke(wire, timeout_seconds=1))
    assert parse_response(canonical_json(result), wire)["schema_version"] == "stage6-equivalence-response-v2"
    legacy = {**result, "schema_version": "stage6-equivalence-response-v1"}
    with pytest.raises(ValidationError):
        parse_response(canonical_json(legacy), wire)
    for mutation in ({"solution": "leak"}, {"student_id": "leak"}):
        with pytest.raises(ValidationError):
            ContextualEquivalenceRequest.model_validate({**wire, **mutation})
        with pytest.raises(ValidationError):
            ContextualEquivalenceRequest.model_validate({**wire, "answer_context": {**wire["answer_context"], **mutation}})
    legacy_request = {k: v for k, v in wire.items() if k != "answer_context"}
    legacy_request["schema_version"] = "stage6-equivalence-request-v1"
    legacy_result = FakeEquivalenceAdapter().invoke(legacy_request, timeout_seconds=1)
    assert parse_response(legacy_result, legacy_request)["schema_version"] == "stage6-equivalence-response-v1"
    with pytest.raises(ValidationError):
        payload_for(legacy_request, {"model": "offline", "max_output_tokens": 512})
