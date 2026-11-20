"""S5 uses isolated transactions and fabricated provider records, never paid APIs."""
from copy import deepcopy
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import func, select

from app.core.config import get_settings
from app.core.database import get_db
from app.main import app
from app.models import (AssessmentCall, AssessmentTask, Assignment, Attempt, AttemptRevision,
                        KnowledgePoint, PaperInstance, PaperInstanceItem, StudentMastery)
from app.schemas.assessment import AdoptPaperEquivalence, PaperEquivalenceRequest, QualityFeedbackIn
from app.schemas.submissions import AttemptReviewRequest, ManualAnswerIn, ManualSubmissionRequest
from app.services import paper_equivalence as service, submission_workflow as grading
from app.services.assessment import AssessmentProblem, now, quality_feedback
from app.services.equivalence_candidates import classify_unit
from tests.test_stage6_s1 import context as base_context, protected_counts


@pytest.fixture
def context(base_context, monkeypatch):
    db, factory, original, _, question = base_context
    reference = {"kind": "group", "schema_version": "answer-units-v1", "items": [
        {"item_id": "sub_1.blank_1", "subquestion_no": 1, "blank_no": 1, "answer": "相等", "answer_match_type": "exact"},
        {"item_id": "sub_2.blank_1", "subquestion_no": 2, "blank_no": 1, "answer": "负", "answer_match_type": "exact"},
        {"item_id": "sub_3.blank_1", "subquestion_no": 3, "blank_no": 1, "answer": "30", "answer_match_type": "numeric"},
    ]}
    assignment = Assignment(title="S5 fixture", chapter_code="s1")
    db.add(assignment); db.flush()
    paper = PaperInstance(assignment_id=assignment.id, student_id=original.student_id,
        version_no=1, status="draft", instance_code="S5-" + uuid4().hex)
    db.add(paper); db.flush()
    from app.services import knowledge_catalog
    point = db.get(KnowledgePoint, question.main_knowledge_point_id)
    formal = knowledge_catalog.formal_kps_by_code()
    monkeypatch.setattr(knowledge_catalog, "formal_kps_by_code", lambda: {
        **formal, point.code: {**next(iter(formal.values())), "code": point.code}})
    item = PaperInstanceItem(paper_instance_id=paper.id, question_id=question.id, order_no=3,
        question_snapshot={"question_type": "fill_blank", "question_text": "有理数法则：\n（1）两个数的绝对值________。\n（2）和的符号为________。\n（3）计算结果是________。", "difficulty": 2,
            "original_qid": question.original_qid, "core_literacy": knowledge_catalog.formal_literacy_dimensions()[0]},
        answer_snapshot=deepcopy(reference), answer_rule_snapshot={"answer_match_type": "group", "answer_normalization_rule": {}},
        knowledge_point_snapshot={"id": point.code, "name": point.name}, selection_audit={"source_module": "history"})
    db.add(item)
    question.correct_answer = deepcopy(reference)
    db.flush()
    paper.status, paper.published_at = "published", now()
    db.flush()
    return db, factory, paper, item, question


def submit(context, *, wrong_numeric=False, image=True):
    db, _, paper, item, question = context
    request = ManualSubmissionRequest(student_id=paper.student_id, idempotency_key="s5-" + uuid4().hex,
        answers=[ManualAnswerIn(paper_instance_item_id=item.id, order_no=item.order_no, response_status="answered",
            answer={"kind": "group", "items": [{"item_id": "sub_1.blank_1", "answer": "一样"},
                {"item_id": "sub_2.blank_1", "answer": "负数"},
                {"item_id": "sub_3.blank_1", "answer": "31" if wrong_numeric else "+30"}]})])
    if image:
        result = grading.create_confirmed_image_submission(db, paper.id, request, {"kind": "stage6_transcription", "reviewer": "fixture"})
    else:
        result = grading.create_manual_submission(db, paper.id, request)
    db.flush()
    return result


def audit_request(report, **kwargs):
    return PaperEquivalenceRequest(expected_input_sha256=report["input_sha256"], reviewer="S5测试教师", reason="整卷核对", **kwargs)


def complete(db, paper, *, verdict="equivalent", fake=False):
    tasks = list(db.scalars(select(AssessmentTask).where(
        AssessmentTask.snapshot["paper_instance_id"].astext == str(paper.id))))
    for task in tasks:
        task.provider = "fake" if fake else "bailian"
        task.status, task.attempt_count, task.completed_at = "succeeded", 1, now()
        task.result = {"schema_version": "stage6-equivalence-response-v2", "request_id": task.request_payload["request_id"],
            "verdict": verdict, "reason_code": {"equivalent": "contextual_match", "not_equivalent": "contextual_mismatch", "uncertain": "context_required"}[verdict],
            "explanation": "离线HTTP结果夹具"}
        db.add(AssessmentCall(task_id=task.id, call_no=1, lease_token=uuid4(), status="succeeded",
            raw_response="offline fixture", parsed_response=deepcopy(task.result),
            usage={"synthetic": fake, "usage_status": "known", "cost_cny": 0}, started_at=now(), completed_at=now()))
    db.flush()
    return tasks


def adoption(report, tasks):
    return AdoptPaperEquivalence(**audit_request(report).model_dump(), task_ids=[t.id for t in tasks])


def test_preview_schedule_reuse_do_not_write_grades_or_send_other_answers(context):
    db, _, paper, item, _ = context
    submit(context)
    db.autoflush = False  # Match the production session configuration.
    before = protected_counts(db)
    report = service.preview(db, paper.id)
    assert report["candidate_count"] == report["new_task_count"] == 2
    assert not report["units"][2]["eligible"]  # +30 is already numerically equal.
    assert db.scalar(select(func.count()).select_from(AssessmentTask)) == 0
    queued = service.schedule(db, paper.id, audit_request(report))
    assert queued["created_count"] == 2 and queued["new_task_count"] == 0
    repeated = service.schedule(db, paper.id, audit_request(report))
    assert repeated["created_count"] == 0 and repeated["reused_count"] == 2
    tasks = list(db.scalars(select(AssessmentTask)))
    assert {t.request_payload["student_answer"] for t in tasks} == {"一样", "负数"}
    for task in tasks:
        assert set(task.request_payload) == {"schema_version", "request_id", "reference_answer", "student_answer", "answer_context"}
        assert "PRIVATE_" not in str(task.request_payload)
    assert protected_counts(db) == before


@pytest.mark.parametrize("wrong_numeric,correct", [(False, True), (True, False)])
def test_teacher_adoption_preserves_original_answers_and_group_rules_with_profile_revision(context, wrong_numeric, correct):
    db, _, paper, item, _ = context
    submit(context, wrong_numeric=wrong_numeric)
    report = service.preview(db, paper.id)
    service.schedule(db, paper.id, audit_request(report))
    tasks = complete(db, paper)
    attempt = db.scalar(select(Attempt).where(Attempt.paper_instance_item_id == item.id))
    original = deepcopy(attempt.student_answer)
    snapshot = deepcopy(item.answer_snapshot)
    request = adoption(service.preview(db, paper.id), tasks)
    result = service.adopt(db, paper.id, request)
    assert result.items[0].attempt.is_correct is correct
    assert attempt.grade_status == "teacher_confirmed" and attempt.manual_override
    assert attempt.revision_no == 2 and attempt.student_answer == original
    assert item.answer_snapshot == snapshot and attempt.grading_details["auto_is_correct"] is False
    assert len(attempt.grading_details["equivalence_review"]["decisions"]) == 2
    assert db.scalar(select(func.count()).select_from(AttemptRevision).where(AttemptRevision.attempt_id == attempt.id)) == 2
    assert result.mastery_update_stats is not None and result.items[0].attempt.used_in_profile
    mastery = db.scalar(select(StudentMastery).where(StudentMastery.student_id == paper.student_id))
    assert mastery.attempt_count == 1 and mastery.correct_count == int(correct)
    assert service.adopt(db, paper.id, request).replayed
    assert attempt.revision_no == 2
    assert mastery.attempt_count == 1
    assert service.preview(db, paper.id)["candidate_count"] == 0


@pytest.mark.parametrize("failure", ["partial", "uncertain", "fake", "no_call", "late_call", "wrong_call"])
def test_incomplete_synthetic_or_unverified_results_cannot_be_adopted(context, failure):
    db, _, paper, item, _ = context
    submit(context)
    report = service.preview(db, paper.id)
    service.schedule(db, paper.id, audit_request(report))
    tasks = complete(db, paper, verdict="uncertain" if failure == "uncertain" else "equivalent", fake=failure == "fake")
    if failure in {"no_call", "late_call", "wrong_call"}:
        call = db.scalar(select(AssessmentCall).where(AssessmentCall.task_id == tasks[0].id))
        if failure == "no_call":
            db.delete(call)
        elif failure == "late_call":
            call.late_response = True
        else:
            call.parsed_response = {"verdict": "equivalent"}
        db.flush()
    request = adoption(service.preview(db, paper.id), tasks[:1] if failure == "partial" else tasks)
    before = protected_counts(db)
    with pytest.raises(AssessmentProblem):
        service.adopt(db, paper.id, request)
    assert protected_counts(db) == before
    assert db.scalar(select(Attempt).where(Attempt.paper_instance_item_id == item.id)).revision_no == 1


def test_changed_attempt_blocks_old_scan_and_suggestions(context):
    db, _, paper, item, _ = context
    submit(context)
    report = service.preview(db, paper.id)
    service.schedule(db, paper.id, audit_request(report))
    tasks = complete(db, paper)
    attempt = db.scalar(select(Attempt).where(Attempt.paper_instance_item_id == item.id))
    grading.review_attempt(db, attempt.id, AttemptReviewRequest(action="confirm", expected_revision_no=1,
        is_correct=False, actor="other", reason="教师改判"))
    for operation in (lambda: service.schedule(db, paper.id, audit_request(report)),
                      lambda: service.adopt(db, paper.id, adoption(report, tasks))):
        with pytest.raises(AssessmentProblem) as exc:
            operation()
        assert exc.value.code == "INPUT_STALE"
    assert attempt.revision_no == 2 and service.preview(db, paper.id)["candidate_count"] == 0


def test_profile_failure_rolls_back_entire_adoption(context, monkeypatch):
    db, _, paper, item, _ = context
    submit(context)
    report = service.preview(db, paper.id)
    service.schedule(db, paper.id, audit_request(report))
    tasks = complete(db, paper)
    request = adoption(service.preview(db, paper.id), tasks)
    db.commit()
    def fail(*args):
        raise RuntimeError("profile fixture failed")
    monkeypatch.setattr(grading, "refresh_student_profiles", fail)
    with pytest.raises(RuntimeError), db.begin_nested():
        service.adopt(db, paper.id, request)
    db.expire_all()
    attempt = db.scalar(select(Attempt).where(Attempt.paper_instance_item_id == item.id))
    assert attempt.revision_no == 1 and attempt.is_correct is False
    assert not any(e.get("action") == "teacher_adopt" for t in db.scalars(select(AssessmentTask)) for e in t.action_history)


def test_known_bad_quality_feedback_blocks_adoption(context):
    db, _, paper, _, _ = context
    submit(context)
    report = service.preview(db, paper.id)
    service.schedule(db, paper.id, audit_request(report))
    tasks = complete(db, paper)
    quality_feedback(db, "equivalence", tasks[0].id, tasks[0].snapshot["item_id"], QualityFeedbackIn(
        expected_revision=0, review_status="incorrect", error_type="equivalence_error",
        gold={"verdict": "not_equivalent"}, reviewer="fixture", reason="核对发现判断错误"))
    report = service.preview(db, paper.id)
    assert not all(u["task"]["adoptable"] for u in report["units"] if u["eligible"])
    with pytest.raises(AssessmentProblem):
        service.adopt(db, paper.id, adoption(report, tasks))


def test_adoption_blocker_identifies_uncertain_leaf_without_blocking_determinate_mismatch(context):
    db, _, paper, item, _ = context
    submit(context)
    report = service.preview(db, paper.id)
    service.schedule(db, paper.id, audit_request(report))
    tasks = complete(db, paper, verdict="not_equivalent")
    assert all(u["task"]["adoptable"] for u in service.preview(db, paper.id)["units"] if u["eligible"])
    uncertain = tasks[1]
    uncertain.result = {**uncertain.result, "verdict": "uncertain", "reason_code": "context_required"}
    call = db.scalar(select(AssessmentCall).where(AssessmentCall.task_id == uncertain.id))
    call.parsed_response = deepcopy(uncertain.result); db.flush()
    report = service.preview(db, paper.id)
    candidates = [u for u in report["units"] if u["eligible"]]
    blocked = next(u for u in candidates if u["task"]["id"] == str(uncertain.id))
    assert blocked["task"]["adoption_blocker"]["code"] == "model_uncertain"
    assert "人工复核" in blocked["task"]["adoption_blocker"]["message"]
    other = next(u for u in candidates if u is not blocked)
    assert other["task"]["adoptable"] and other["task"]["adoption_blocker"] is None
    with pytest.raises(AssessmentProblem) as exc:
        service.adopt(db, paper.id, adoption(report, tasks))
    assert exc.value.code == "ADOPTION_NOT_READY"
    assert db.scalar(select(Attempt).where(Attempt.paper_instance_item_id == item.id)).revision_no == 1


def test_foreign_task_is_rejected_without_partial_write(context):
    db, _, paper, item, _ = context
    submit(context)
    report = service.preview(db, paper.id)
    service.schedule(db, paper.id, audit_request(report))
    tasks = complete(db, paper)
    tasks[0].snapshot = {**tasks[0].snapshot, "paper_instance_id": str(uuid4())}; db.flush()
    with pytest.raises(AssessmentProblem) as exc:
        service.adopt(db, paper.id, adoption(report, tasks))
    assert exc.value.code == "TASK_SCOPE_MISMATCH"
    assert db.scalar(select(Attempt).where(Attempt.paper_instance_item_id == item.id)).revision_no == 1


def test_current_question_edits_do_not_silently_replace_frozen_grading_reference(context):
    db, _, paper, item, question = context
    submit(context)
    report = service.preview(db, paper.id)
    question.correct_answer = {**deepcopy(question.correct_answer), "items": [
        {**p, "answer": "已经改变的标答"} for p in question.correct_answer["items"]]}
    db.flush()
    assert service.preview(db, paper.id)["input_sha256"] == report["input_sha256"]
    service.schedule(db, paper.id, audit_request(report))
    tasks = complete(db, paper, verdict="not_equivalent")
    assert {t.request_payload["reference_answer"] for t in tasks} == {"相等", "负"}
    output = service.adopt(db, paper.id, adoption(service.preview(db, paper.id), tasks))
    assert output.summary.incorrect_count == 1
    assert item.answer_snapshot["items"][0]["answer"] == "相等"


def test_unsubmitted_and_unconfirmed_image_are_not_scheduled(context):
    db, _, paper, item, _ = context
    with pytest.raises(AssessmentProblem) as exc:
        service.preview(db, paper.id)
    assert exc.value.code == "SUBMISSION_REQUIRED"
    submit(context)
    attempt = db.scalar(select(Attempt).where(Attempt.paper_instance_item_id == item.id))
    details = deepcopy(attempt.grading_details); details.pop("recognition_reviewed")
    attempt.grading_details = details; db.flush()
    report = service.preview(db, paper.id)
    assert report["candidate_count"] == 0
    assert all(u["reason_code"] == "input_unconfirmed" for u in report["units"])


@pytest.mark.parametrize("normalization", [{"require_unit": True}, {"allowed_terms": ["相等"]}])
def test_short_text_extra_constraints_remain_manual(normalization):
    result = classify_unit(question_type="fill_blank", reference="相等", student_answer="一样", item_id="answer",
        rule={"answer_match_type": "exact", "answer_normalization_rule": normalization})
    assert not result["eligible"] and result["reason_code"] == "format_review_required"


def test_http_routes_and_disabled_switch(context, monkeypatch):
    db, _, paper, item, _ = context
    submit(context)
    app.dependency_overrides[get_db] = lambda: db
    try:
        client = TestClient(app)
        prefix = f"/api/v1/internal/stage6/paper-instances/{paper.id}"
        report = client.get(prefix + "/equivalence").json()
        payload = audit_request(report).model_dump(mode="json")
        assert client.post(prefix + "/equivalence-tasks", json=payload).json()["created_count"] == 2
        assert client.post(prefix + "/equivalence-adoptions", json={**payload, "task_ids": []}).status_code == 422
        monkeypatch.setattr(get_settings(), "stage6_enabled", False)
        assert client.get(prefix + "/equivalence").status_code == 403
        assert client.post(prefix + "/equivalence-tasks", json=payload).status_code == 403
    finally:
        app.dependency_overrides.pop(get_db, None)
