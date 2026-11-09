"""Cross-module contracts for pool eligibility, history and profile maintenance."""
from copy import deepcopy
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.models import Assignment, KnowledgePoint, PaperInstance, Question, SchoolClass, Student, StudentKnowledgeStatus
from app.services.paper_snapshots import new_item, snapshot_payload
from app.services.profile_rebuild import materialized_profile, rebuild_student_profile
from app.services.profile_refresh import refresh_student_profiles
from app.services.recommendations import recommendation_candidates, required_question_candidates, history_recommendation_candidates
from app.services.student_profile import load_context, build_profile
from app.services.weekly_summary import _candidates, _false_knowledge_points
from app.services.personalized_preview import personalized_question_candidates
from scripts.rebuild_profiles import selected_students
from tests.test_profile_api import db, client


@pytest.fixture
def student(db):
    school_class = SchoolClass(name="画像维护班-" + uuid4().hex[:12])
    db.add(school_class)
    db.flush()
    value = Student(student_code="MAINT-" + uuid4().hex[:16], display_name="画像维护测试",
                    class_id=school_class.id, class_name=school_class.name, is_demo=True)
    db.add(value)
    db.flush()
    return value


def question(db, qid=None):
    if qid:
        return db.scalar(select(Question).where(Question.original_qid == qid))
    return db.scalar(select(Question).where(Question.grading_readiness == "machine_ready",
                                            Question.question_type == "fill_blank",
                                            Question.archived_at.is_(None)))


def paper(db, student, question, *, published=True, audit=None, payload=None):
    assignment = Assignment(title="画像维护回归", chapter_code=question.chapter_code,
                            class_id=student.class_id, assignment_type="practice")
    db.add(assignment)
    db.flush()
    result = PaperInstance(assignment_id=assignment.id, student_id=student.id, version_no=1,
                           status="draft", recommendation_audit={}, edit_history=[], edit_revision=1)
    db.add(result)
    db.flush()
    item = new_item(paper_id=result.id, order_no=1, payload=payload or snapshot_payload(question),
                    selection_audit=audit or {"source_module": "required"})
    db.add(item)
    db.flush()
    if published:
        result.status = "published"
        result.published_at = datetime.now(timezone.utc)
        result.instance_code = "TEST-" + uuid4().hex
        db.flush()
    return result, item


def submit(client, student, paper, item, *, blank=False):
    response = client.post(f"/api/v1/paper-instances/{paper.id}/submissions/manual", json={
        "student_id": str(student.id), "idempotency_key": "MAINT-" + uuid4().hex,
        "answers": [{"paper_instance_item_id": str(item.id), "order_no": 1,
                     "response_status": "blank" if blank else "answered",
                     "answer": None if blank else item.answer_snapshot}],
    })
    assert response.status_code == 201, response.text
    return response.json()


def test_080_cannot_reenter_any_ordinary_pool_even_with_stale_ready_flag(db):
    q = question(db, "q.math.g6.rational.school.080")
    q.grading_readiness = "machine_ready"
    q.difficulty = 3
    db.flush()
    point = db.get(KnowledgePoint, q.main_knowledge_point_id)
    pools = [recommendation_candidates(db, q.chapter_code),
             required_question_candidates(db, [q.chapter_code], [q.grade]),
             history_recommendation_candidates(db, [point.code]),
             _candidates(db, {point.id}).get(point.id, []),
             personalized_question_candidates(db, grade_codes=[q.grade], chapter_codes=[q.chapter_code],
                 knowledge_point_codes=[point.code], literacy_name=point.core_competencies[0])]
    assert all(q.id not in {candidate.question_id for candidate in pool} for pool in pools)


def test_080_promotion_is_rejected_but_existing_final_answer_grading_is_preserved(client, db, student):
    q = question(db, "q.math.g6.rational.school.080")
    q.grading_readiness = "raw_answer_only"
    db.flush()
    before = deepcopy(q.correct_answer)
    response = client.post(f"/api/v1/questions/{q.id}/promote", json={
        "expected_review_revision": q.review_revision, "reviewer_name": "回归测试",
        "final_answer_only": True,
        "items": [{"item_id": item["item_id"], "answer": item["answer"],
                   "answer_match_type": item["answer_match_type"]} for item in before["items"]],
    })
    assert response.status_code == 422, response.text
    assert q.correct_answer == before and q.grading_readiness == "raw_answer_only"
    legacy, item = paper(db, student, q)
    result = submit(client, student, legacy, item)
    attempt = result["items"][0]["attempt"]
    assert attempt["is_correct"] is True
    assert attempt["used_in_profile"] is False
    assert attempt["exclusion_reason"] == "unsupported_profile_question_type"
    profile = build_profile(load_context(db, student.id))
    assert profile.summary.valid_attempt_count == profile.summary.invalid_evidence_count == 0
    assert all(item.score == 60 for item in profile.literacies)
    assert profile.excluded_evidence[0].reason == "unsupported_profile_question_type"


def test_new_080_draft_cannot_be_published(client, db, student):
    draft, _ = paper(db, student, question(db, "q.math.g6.rational.school.080"), published=False)
    response = client.post(f"/api/v1/paper-instances/{draft.id}/publish",
                           json={"expected_edit_revision": draft.edit_revision})
    assert response.status_code == 422, response.text
    assert draft.status == "draft"


def test_withdrawn_080_paper_cannot_be_republished(db, student):
    from app.services.paper_workflow import PaperValidation, republish_paper
    old, _ = paper(db, student, question(db, "q.math.g6.rational.school.080"))
    old.invalidated_at = datetime.now(timezone.utc)
    old.invalidation_type = "withdrawn"
    db.flush()
    with pytest.raises(PaperValidation, match="计算题"):
        republish_paper(db, old.id, expected_revision=old.edit_revision)
    assert old.invalidated_at is not None


def test_archiving_and_reclassifying_questions_preserve_all_historical_profiles(client, db, student):
    q = question(db)
    source, source_item = paper(db, student, q)
    source_result = submit(client, student, source, source_item, blank=True)
    source_attempt = source_result["items"][0]["attempt"]
    review, review_item = paper(db, student, q, audit={"source_module": "history", "evidence": [
        {"attempt_id": source_attempt["id"], "revision_no": 1}]})
    submit(client, student, review, review_item, blank=True)
    old_point = db.scalar(select(KnowledgePoint).where(KnowledgePoint.code == source_item.knowledge_point_snapshot["id"]))
    assert db.get(StudentKnowledgeStatus, (student.id, old_point.id)).status == "no"
    before = materialized_profile(db, student.id)
    snapshots = deepcopy((source_item.knowledge_point_snapshot, review_item.knowledge_point_snapshot))
    q.main_knowledge_point_id = db.scalar(select(KnowledgePoint.id).where(KnowledgePoint.id != old_point.id))
    q.archived_at = datetime.now(timezone.utc)
    db.flush()
    refresh_student_profiles(db, student.id)
    assert materialized_profile(db, student.id) == before
    assert (source_item.knowledge_point_snapshot, review_item.knowledge_point_snapshot) == snapshots
    profile = build_profile(load_context(db, student.id))
    assert profile.summary.valid_attempt_count == 2
    assert {record.evidence.knowledge_point_id for record in load_context(db, student.id).records} == {old_point.id}
    weekly_points = _false_knowledge_points(db, source_assignment_ids=[review.assignment_id], student_ids=[student.id])
    assert set(weekly_points[student.id]) == {old_point.id}
    teacher = client.get(f"/api/v1/paper-instances/{source.id}").json()
    assert teacher["items"][0]["knowledge_point_snapshot"] == snapshots[0]


def test_student_list_profile_and_class_statistics_share_effective_evidence(client, db, student):
    q = question(db)
    confirmed, item = paper(db, student, q)
    submitted = submit(client, student, confirmed, item)
    pending, pending_item = paper(db, student, q)
    submitted_pending = submit(client, student, pending, pending_item)
    attempt = submitted_pending["items"][0]["attempt"]
    response = client.patch(f"/api/v1/attempts/{attempt['id']}/review", json={
        "action": "reopen", "expected_revision_no": attempt["revision_no"], "reason": "维护口径测试"})
    assert response.status_code == 200, response.text
    obsolete, obsolete_item = paper(db, student, q)
    submit(client, student, obsolete, obsolete_item)
    obsolete.invalidated_at = datetime.now(timezone.utc)
    obsolete.invalidation_type = "superseded"
    db.flush()
    refresh_student_profiles(db, student.id)
    profile = client.get(f"/api/v1/students/{student.id}/profile").json()
    detail = client.get(f"/api/v1/students/{student.id}").json()
    listing = client.get("/api/v1/students", params={"keyword": student.student_code}).json()["items"][0]
    assert detail["attempt_count"] == listing["attempt_count"] == profile["summary"]["valid_attempt_count"] == 1
    assert detail["total_attempt_count"] == listing["total_attempt_count"] == profile["summary"]["total_attempt_count"] == 3
    assert detail["pending_review_count"] == profile["summary"]["pending_review_count"] == 1
    assert detail["correct_count"] == sum(point["correct_count"] for point in profile["masteries"]) == 1
    overview = client.get("/api/v1/analytics/class-overview", params={
        "class_name": student.class_name, "chapter_code": q.chapter_code}).json()
    code = item.knowledge_point_snapshot["id"]
    point = next(point for point in overview["knowledge_points"] if point["knowledge_point_code"] == code)
    assert point["confirmed_attempt_count"] == detail["attempt_count"]
    assert overview["summary"]["pending_review_count"] == 1
    assert overview["summary"]["invalid_evidence_count"] == 0
    assert submitted["items"][0]["attempt"]["used_in_profile"]


def test_rebuild_skips_archived_students_and_dry_run_leaves_no_materializations(db, student):
    archived = Student(student_code="ARCH-" + uuid4().hex[:16], display_name="已归档",
                       class_name=student.class_name, class_id=student.class_id,
                       archived_at=datetime.now(timezone.utc))
    db.add(archived)
    db.flush()
    ids = {row.id for row in selected_students(db)}
    assert student.id in ids and archived.id not in ids
    assert student.id in {row.id for row in selected_students(db, class_id=student.class_id)}
    with pytest.raises(ValueError, match="归档"):
        selected_students(db, student_code=archived.student_code)
    before = materialized_profile(db, student.id)
    now = datetime.now(timezone.utc)
    report = rebuild_student_profile(db, student.id, as_of=now, dry_run=True)
    assert "literacy" in report["changed_components"]
    assert materialized_profile(db, student.id) == before
    rebuild_student_profile(db, student.id, as_of=now, dry_run=False)
    second = rebuild_student_profile(db, student.id, as_of=now, dry_run=False)
    assert second["changed_components"] == []


def test_rebuild_failure_rolls_back_all_three_profiles_and_other_students_can_continue(client, db, student):
    q = question(db)
    bad_payload = snapshot_payload(q)
    bad_payload["question_snapshot"]["core_literacy"] = "非法素养"
    broken, item = paper(db, student, q, payload=bad_payload)
    # Simulate a historical confirmed record whose published metadata is invalid.
    from app.models import Submission, Attempt, AttemptRevision
    submission = Submission(paper_instance_id=broken.id, student_id=student.id, source="manual",
                            idempotency_key="MAINT-" + uuid4().hex, submitted_at=datetime.now(timezone.utc))
    db.add(submission)
    db.flush()
    attempt = Attempt(submission_id=submission.id, paper_instance_item_id=item.id, student_id=student.id,
                      question_id=q.id, student_answer=item.answer_snapshot, grade_status="auto_confirmed",
                      is_correct=True, revision_no=1, answered_at=datetime.now(timezone.utc))
    db.add(attempt)
    db.flush()
    db.add(AttemptRevision(attempt_id=attempt.id, revision_no=1, after_state={"is_correct": True},
                           operator_type="system", operator_id="maintenance-test", reason="historical evidence"))
    db.flush()
    before = materialized_profile(db, student.id)
    with pytest.raises(ValueError, match=str(attempt.id)):
        rebuild_student_profile(db, student.id, as_of=datetime.now(timezone.utc), dry_run=False)
    assert materialized_profile(db, student.id) == before
    healthy = Student(student_code="GOOD-" + uuid4().hex[:16], display_name="正常学生", class_name=student.class_name)
    db.add(healthy)
    db.flush()
    result = rebuild_student_profile(db, healthy.id, as_of=datetime.now(timezone.utc), dry_run=False)
    assert result["status"] == "ok" and len(materialized_profile(db, healthy.id)["literacy"]) == 8
