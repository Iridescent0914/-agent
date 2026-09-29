from datetime import datetime, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.core.database import SessionLocal, get_db
from app.main import app
from app.models import (
    Assignment, Attempt, AttemptRevision, PaperInstance, Question,
    QuestionExamPoint, Student, StudentMastery,
)
from app.schemas.recommendations import RecommendationRequest
from app.services.recommendations import preview
from app.services.student_profile import (
    MASTERY_FIELDS, attempt_detail, build_profile, history_for, load_context, persist_mastery,
)


@pytest.fixture
def db():
    with SessionLocal() as session:
        try:
            yield session
        finally:
            session.rollback()


@pytest.fixture
def client(db):
    app.dependency_overrides[get_db] = lambda: db
    try:
        with TestClient(app) as value:
            yield value
    finally:
        app.dependency_overrides.pop(get_db, None)


def first_student(db):
    return db.scalar(select(Student).where(Student.student_code == "S001"))


def counts(db):
    return tuple(db.scalar(select(func.count()).select_from(model)) for model in
                 (Assignment, PaperInstance, Attempt, AttemptRevision, StudentMastery))


def test_profile_history_detail_and_read_only_preview(client, db):
    student = first_student(db)
    before = counts(db)
    profile = client.get(f"/api/v1/students/{student.id}/profile").json()
    assert profile["summary"]["valid_attempt_count"] >= 30
    assert profile["summary"]["assessed_exam_point_count"] == 10
    assert profile["summary"]["unassessed_exam_point_count"] == 44
    assert len(profile["masteries"]) == 54
    assert all(item["mastery"] is None for item in profile["masteries"] if item["attempt_count"] == 0)
    evaluated = [item for item in profile["masteries"] if item["attempt_count"]]
    for item in evaluated:
        history = client.get(f"/api/v1/students/{student.id}/mastery/{item['exam_point_code']}/history").json()
        assert history["points"][-1]["mastery_after"] == item["mastery"]
        assert len(history["points"]) == item["attempt_count"]
    attempt_id = history["points"][0]["attempt_id"]
    detail = client.get(f"/api/v1/students/{student.id}/attempts/{attempt_id}").json()
    assert detail["used_in_profile"] and detail["revision"]["revision_no"] == detail["revision_no"]
    assert detail["question_snapshot"]["original_qid"]
    other = db.scalar(select(Student).where(Student.student_code == "S002"))
    assert client.get(f"/api/v1/students/{other.id}/attempts/{attempt_id}").status_code == 404
    response = client.post(f"/api/v1/students/{student.id}/recommendations/preview", json={
        "review_exam_point_codes": [item["exam_point_code"] for item in evaluated], "review_count": 6,
    })
    assert response.status_code == 200
    output = response.json()
    assert output["selected_count"] + output["shortfall"] == 6
    assert output["rule_version"] == "m2-v1"
    assert counts(db) == before


def test_snapshot_source_survives_current_question_changes(client, db):
    context = load_context(db, first_student(db).id)
    record = context.records[0]
    code = record.item.exam_point_snapshot["main"]["id"]
    original_text = record.item.question_snapshot["question_text"]
    expected = history_for(context, code)
    question = db.get(Question, record.attempt.question_id)
    question.question_text = "changed current question"
    question.difficulty = 5 if question.difficulty != 5 else 1
    link = db.scalar(select(QuestionExamPoint).where(QuestionExamPoint.question_id == question.id,
                                                   QuestionExamPoint.relation_type == "main"))
    link.relation_type = "related"
    db.flush()
    fresh = load_context(db, context.student.id)
    assert history_for(fresh, code).points == expected.points
    assert attempt_detail(fresh.records[0]).question_snapshot["question_text"] == original_text
    response = client.get(f"/api/v1/students/{context.student.id}/attempts",
                          params={"exam_point_code": code, "page_size": 1}).json()
    assert response["total"] == expected.mastery.attempt_count
    assert response["items"][0]["main_exam_point_code"] == code


def test_rebuild_idempotence_revision_and_withdrawal(db):
    student = first_student(db)
    context = load_context(db, student.id)
    initial_valid_count = build_profile(context).summary.valid_attempt_count
    before_counts = counts(db)[:4]
    persist_mastery(db, context)
    before = {row.exam_point_id: (tuple(getattr(row, field) for field in MASTERY_FIELDS), row.updated_at)
              for row in db.scalars(select(StudentMastery).where(StudentMastery.student_id == student.id))}
    second = persist_mastery(db, load_context(db, student.id))
    assert second == {"inserted": 0, "updated": 0, "deleted": 0, "unchanged": 10}
    after = {row.exam_point_id: (tuple(getattr(row, field) for field in MASTERY_FIELDS), row.updated_at)
             for row in db.scalars(select(StudentMastery).where(StudentMastery.student_id == student.id))}
    assert before == after and counts(db)[:4] == before_counts

    record = context.records[0]
    attempt = record.attempt
    attempt.is_correct = not attempt.is_correct
    attempt.revision_no += 1
    db.add(AttemptRevision(attempt_id=attempt.id, revision_no=attempt.revision_no,
                           before_state=None, after_state={"is_correct": attempt.is_correct},
                           operator_type="teacher", reason="transaction-only test"))
    db.flush()
    corrected = load_context(db, student.id)
    persist_mastery(db, corrected)
    assert build_profile(corrected).summary.valid_attempt_count == initial_valid_count
    code = record.evidence.exam_point_code
    point = next(point for point in history_for(corrected, code).points if point.attempt_id == attempt.id)
    assert point.revision_no == 2 and point.answered_at == attempt.answered_at

    for item in corrected.records:
        if item.evidence and item.evidence.exam_point_code == code:
            item.attempt.grade_status, item.attempt.is_correct = "needs_review", None
    db.flush()
    withdrawn = load_context(db, student.id)
    persist_mastery(db, withdrawn)
    assert history_for(withdrawn, code).mastery.mastery is None
    assert db.get(StudentMastery, (student.id, record.evidence.exam_point_id)) is None


def test_empty_student_and_invalid_evidence(client, db):
    student = Student(student_code="TEST-" + uuid4().hex[:16], display_name="测试学生", class_name="测试", is_demo=True)
    db.add(student)
    db.flush()
    response = client.get(f"/api/v1/students/{student.id}/profile")
    assert response.status_code == 200
    body = response.json()
    assert body["summary"]["valid_attempt_count"] == 0
    assert all(item["mastery"] is None for item in body["masteries"])
    existing = first_student(db)
    record = load_context(db, existing.id).records[0]
    record.attempt.is_correct = None
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()


def test_api_validation_and_cors(client, db):
    student = first_student(db)
    endpoint = f"/api/v1/students/{student.id}/recommendations/preview"
    assert client.post(endpoint, json={}).status_code == 422
    response = client.post(endpoint, json={"review_exam_point_codes": ["nonexistent"],
                                           "review_count": 2})
    assert response.status_code == 422
    assert response.json()["code"] == "http_422"
    assert client.get(f"/api/v1/students/{uuid4()}/profile").status_code == 404
    assert client.get("/api/v1/students/S001/profile").status_code == 422
    cors = client.options(endpoint, headers={"Origin": "http://localhost:5173",
                                             "Access-Control-Request-Method": "POST",
                                             "Access-Control-Request-Headers": "content-type"})
    assert cors.status_code == 200
    assert "POST" in cors.headers["access-control-allow-methods"]
    denied = client.options(endpoint, headers={"Origin": "http://invalid.example",
                                               "Access-Control-Request-Method": "POST"})
    assert denied.status_code == 400


def test_fixed_time_recommendations_are_repeatable(db):
    context = load_context(db, first_student(db).id)
    codes = [exam.code for exam in context.catalog if exam.available_question_count]
    params = RecommendationRequest(review_exam_point_codes=codes, review_count=6)
    at = datetime(2026, 9, 23, tzinfo=timezone.utc)
    first = preview(db, context, params, as_of=at)
    assert first == preview(db, context, params, as_of=at)
    assert first.selected_count == 6
