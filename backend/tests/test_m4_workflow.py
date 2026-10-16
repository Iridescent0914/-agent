from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.core.database import SessionLocal, get_db
from app.main import app
from app.models import (
    Attempt, AttemptRevision, PaperInstanceItem, Question, Student,
    StudentMastery, Submission,
)
from app.services.student_profile import load_context


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


def _published_paper(client, db):
    student = db.scalar(select(Student).where(Student.student_code == "S002"))
    request = {
        "idempotency_key": "m4-assignment-" + uuid4().hex,
        "title": "M4 批改闭环测试",
        "student_ids": [str(student.id)],
        "recommendation_parameters": {
            "review_exam_point_codes": [
                "math.g6.rational.tracking.08_multiply_divide",
                "math.g6.rational.tracking.07_add_subtract",
            ],
            "review_count": 3,
            "avoid_recent_days": 0,
            "max_exam_point_ratio": 0.67,
        },
    }
    assignment = client.post("/api/v1/assignments", json=request)
    assert assignment.status_code == 201, assignment.text
    paper = assignment.json()["papers"][0]
    published = client.post(
        f"/api/v1/paper-instances/{paper['id']}/publish",
        json={"expected_edit_revision": paper["edit_revision"]},
    )
    assert published.status_code == 200, published.text
    return student, published.json()


def _answers(paper):
    return [
        {
            "paper_instance_item_id": item["id"],
            "order_no": item["order_no"],
            "response_status": "answered",
            "answer": item["answer_snapshot"],
        }
        for item in paper["items"]
    ]


def test_manual_submission_is_idempotent_and_updates_mastery(client, db):
    student, paper = _published_paper(client, db)
    before_attempts = int(db.scalar(select(func.count(Attempt.id))) or 0)
    body = {
        "idempotency_key": "m4-submit-" + uuid4().hex,
        "student_id": str(student.id),
        "answers": _answers(paper),
    }
    response = client.post(
        f"/api/v1/paper-instances/{paper['id']}/submissions/manual", json=body,
    )
    assert response.status_code == 201, response.text
    result = response.json()
    assert result["summary"] == {
        "total_count": 3, "correct_count": 3,
        "incorrect_count": 0, "needs_review_count": 0,
    }
    assert result["mastery_update_stats"] is not None
    assert all(item["attempt"]["revision_no"] == 1 for item in result["items"])
    assert int(db.scalar(select(func.count(Attempt.id))) or 0) == before_attempts + 3
    assert db.scalar(select(func.count(Submission.id)).where(
        Submission.paper_instance_id == paper["id"]
    )) == 1
    assert db.scalar(select(func.count(AttemptRevision.id)).join(
        Attempt, Attempt.id == AttemptRevision.attempt_id
    ).where(Attempt.paper_instance_item_id.in_([item["id"] for item in paper["items"]]))) == 3

    repeated = client.post(
        f"/api/v1/paper-instances/{paper['id']}/submissions/manual", json=body,
    )
    assert repeated.status_code == 201, repeated.text
    assert repeated.json()["replayed"] is True
    assert int(db.scalar(select(func.count(Attempt.id))) or 0) == before_attempts + 3

    same_payload_new_key = body | {"idempotency_key": "m4-submit-" + uuid4().hex}
    replayed = client.post(
        f"/api/v1/paper-instances/{paper['id']}/submissions/manual",
        json=same_payload_new_key,
    )
    assert replayed.status_code == 201 and replayed.json()["replayed"] is True

    changed = deepcopy(body)
    first_answer = changed["answers"][0]["answer"]
    if isinstance(first_answer, dict) and first_answer.get("kind") == "group":
        first_answer["items"][0]["answer"] = "演示不同答案"
    else:
        changed["answers"][0]["answer"] = "演示不同答案"
    conflict = client.post(
        f"/api/v1/paper-instances/{paper['id']}/submissions/manual", json=changed,
    )
    assert conflict.status_code == 409

    context = load_context(db, student.id)
    assert any(
            record.attempt.paper_instance_item_id == UUID(paper["items"][0]["id"])
        and record.problem is None
        for record in context.records
    )


def test_review_revision_stale_conflict_and_reopen(client, db):
    student, paper = _published_paper(client, db)
    body = {
        "idempotency_key": "m4-submit-" + uuid4().hex,
        "student_id": str(student.id),
        "answers": _answers(paper),
    }
    submitted = client.post(
        f"/api/v1/paper-instances/{paper['id']}/submissions/manual", json=body,
    ).json()
    attempt = submitted["items"][0]["attempt"]
    answered_at = attempt["answered_at"]

    changed = client.patch(
        f"/api/v1/attempts/{attempt['id']}/review",
        json={
            "action": "confirm", "expected_revision_no": 1,
            "is_correct": False, "reason": "教师核对后改判为错误",
        },
    )
    assert changed.status_code == 200, changed.text
    current = next(item["attempt"] for item in changed.json()["items"]
                   if item["attempt"]["id"] == attempt["id"])
    assert current["revision_no"] == 2
    assert current["grade_status"] == "teacher_confirmed"
    assert current["is_correct"] is False
    assert datetime.fromisoformat(current["answered_at"].replace("Z", "+00:00")) == datetime.fromisoformat(answered_at.replace("Z", "+00:00"))

    stale = client.patch(
        f"/api/v1/attempts/{attempt['id']}/review",
        json={
            "action": "confirm", "expected_revision_no": 1,
            "is_correct": True, "reason": "陈旧页面请求",
        },
    )
    assert stale.status_code == 409

    reopened = client.patch(
        f"/api/v1/attempts/{attempt['id']}/review",
        json={
            "action": "reopen", "expected_revision_no": 2,
            "is_correct": None, "reason": "需要再次核对纸质原卷",
        },
    )
    assert reopened.status_code == 200, reopened.text
    current = next(item["attempt"] for item in reopened.json()["items"]
                   if item["attempt"]["id"] == attempt["id"])
    assert current["revision_no"] == 3
    assert current["grade_status"] == "needs_review" and current["is_correct"] is None
    assert datetime.fromisoformat(current["answered_at"].replace("Z", "+00:00")) == datetime.fromisoformat(answered_at.replace("Z", "+00:00"))

    no_op = client.patch(
        f"/api/v1/attempts/{attempt['id']}/review",
        json={
            "action": "reopen", "expected_revision_no": 3,
            "is_correct": None, "reason": "重复撤销确认",
        },
    )
    assert no_op.status_code == 200 and no_op.json()["unchanged"] is True
    assert db.get(Attempt, attempt["id"]).revision_no == 3

    pending_context = load_context(db, student.id)
    pending_record = next(record for record in pending_context.records
                          if record.attempt.id == db.get(Attempt, attempt["id"]).id)
    assert pending_record.problem == "needs_review"

    corrected = client.patch(
        f"/api/v1/attempts/{attempt['id']}/review",
        json={
            "action": "correct", "expected_revision_no": 3,
            "is_correct": True, "corrected_response_status": "answered",
            "corrected_answer": body["answers"][0]["answer"],
            "reason": "修正首次录入并确认正确",
        },
    )
    assert corrected.status_code == 200, corrected.text
    current = next(item["attempt"] for item in corrected.json()["items"]
                   if item["attempt"]["id"] == attempt["id"])
    assert current["revision_no"] == 4
    assert current["grade_status"] == "teacher_confirmed" and current["is_correct"] is True
    assert current["answered_at"] == answered_at or datetime.fromisoformat(
        current["answered_at"].replace("Z", "+00:00")
    ) == datetime.fromisoformat(answered_at.replace("Z", "+00:00"))

    revisions = client.get(f"/api/v1/attempts/{attempt['id']}/revisions")
    assert [item["revision_no"] for item in revisions.json()] == [1, 2, 3, 4]
    assert revisions.json()[0]["operator_id"] == "grading_engine"
    assert revisions.json()[-1]["operator_id"] == "demo_teacher"
    context = load_context(db, student.id)
    record = next(record for record in context.records if record.attempt.id == db.get(Attempt, attempt["id"]).id)
    assert record.problem is None


def test_regrade_uses_republished_answer_without_mutating_paper_snapshot(client, db):
    student, paper = _published_paper(client, db)
    answers = _answers(paper)
    target_item = paper["items"][0]
    original_answer = deepcopy(target_item["answer_snapshot"])
    revised_answer = deepcopy(original_answer)
    if isinstance(revised_answer, dict) and revised_answer.get("kind") == "group":
        first = revised_answer["items"][0]
        match_type = first.get("answer_match_type")
        old_value = first.get("answer")
        if match_type == "choice":
            first["answer"] = "A" if old_value != "A" else "B"
        elif match_type == "numeric":
            first["answer"] = "987654"
        else:
            first["answer"] = "修订后的标准答案"
    else:
        revised_answer = "987654"
    answers[0] = answers[0] | {"answer": revised_answer}

    submitted_response = client.post(
        f"/api/v1/paper-instances/{paper['id']}/submissions/manual",
        json={
            "idempotency_key": "m4-regrade-" + uuid4().hex,
            "student_id": str(student.id),
            "answers": answers,
        },
    )
    assert submitted_response.status_code == 201, submitted_response.text
    submitted = submitted_response.json()
    target_attempt = submitted["items"][0]["attempt"]
    assert target_attempt["is_correct"] is False

    question = db.get(Question, UUID(target_item["question_id"]))
    question.correct_answer = deepcopy(revised_answer)
    question.review_revision += 1
    db.flush()

    update_view = client.get(f"/api/v1/submissions/{submitted['submission_id']}")
    assert update_view.status_code == 200
    assert update_view.json()["answer_key_update_count"] == 1
    assert update_view.json()["items"][0]["answer_key_update_available"] is True

    regraded_response = client.post(
        f"/api/v1/submissions/{submitted['submission_id']}/regrade",
        json={"reason": "题库标准答案修正后重新评分", "actor": "测试教师"},
    )
    assert regraded_response.status_code == 200, regraded_response.text
    regraded = regraded_response.json()
    current = regraded["items"][0]
    assert regraded["regraded_count"] == 1
    assert regraded["answer_key_update_count"] == 0
    assert current["attempt"]["revision_no"] == target_attempt["revision_no"] + 1
    assert current["attempt"]["is_correct"] is True
    assert current["standard_answer_source"] == "question_revision"
    assert current["grading_answer_snapshot"] == revised_answer
    assert current["answer_snapshot"] == original_answer
    persisted_item = db.get(PaperInstanceItem, UUID(target_item["id"]))
    assert persisted_item.answer_snapshot == original_answer

    revisions = client.get(f"/api/v1/attempts/{target_attempt['id']}/revisions").json()
    assert revisions[-1]["reason"] == "题库标准答案修正后重新评分"
    assert revisions[-1]["operator_id"] == "测试教师"

    repeated = client.post(
        f"/api/v1/submissions/{submitted['submission_id']}/regrade",
        json={"reason": "重复重新评分检查", "actor": "测试教师"},
    )
    assert repeated.status_code == 200
    assert repeated.json()["unchanged"] is True
    assert db.get(Attempt, UUID(target_attempt["id"])).revision_no == target_attempt["revision_no"] + 1


def test_first_submission_uses_answer_key_republished_before_entry(client, db):
    student, paper = _published_paper(client, db)
    answers = _answers(paper)
    target_item = paper["items"][0]
    original_answer = deepcopy(target_item["answer_snapshot"])
    revised_answer = deepcopy(original_answer)
    if isinstance(revised_answer, dict) and revised_answer.get("kind") == "group":
        first = revised_answer["items"][0]
        if first.get("answer_match_type") == "choice":
            first["answer"] = "A" if first.get("answer") != "A" else "B"
        elif first.get("answer_match_type") == "numeric":
            first["answer"] = "13579"
        else:
            first["answer"] = "录入前修订的标准答案"
    else:
        revised_answer = "13579"

    question = db.get(Question, UUID(target_item["question_id"]))
    question.correct_answer = deepcopy(revised_answer)
    question.review_revision += 1
    db.flush()
    answers[0] = answers[0] | {"answer": revised_answer}

    before_entry = client.get(
        f"/api/v1/paper-instances/{paper['id']}/submission",
    )
    assert before_entry.status_code == 200
    entry_item = before_entry.json()["items"][0]
    assert entry_item["grading_answer_snapshot"] == revised_answer
    assert entry_item["standard_answer_source"] == "question_revision"
    assert entry_item["answer_snapshot"] == original_answer

    response = client.post(
        f"/api/v1/paper-instances/{paper['id']}/submissions/manual",
        json={
            "idempotency_key": "m4-current-key-" + uuid4().hex,
            "student_id": str(student.id),
            "answers": answers,
        },
    )
    assert response.status_code == 201, response.text
    submitted = response.json()
    current = submitted["items"][0]
    assert current["attempt"]["is_correct"] is True
    assert current["standard_answer_source"] == "question_revision"
    assert current["grading_answer_snapshot"] == revised_answer
    assert current["answer_snapshot"] == original_answer
    assert current["answer_key_update_available"] is False
    assert submitted["answer_key_update_count"] == 0

    persisted_item = db.get(PaperInstanceItem, UUID(target_item["id"]))
    assert persisted_item.answer_snapshot == original_answer


def test_invalidated_paper_cannot_start_but_existing_submission_can_be_reviewed(client, db):
    student, paper = _published_paper(client, db)
    withdrawn = client.post(
        f"/api/v1/paper-instances/{paper['id']}/withdraw",
        json={
            "expected_edit_revision": paper["edit_revision"],
            "reason": "测试失效版本",
        },
    )
    assert withdrawn.status_code == 200
    rejected = client.post(
        f"/api/v1/paper-instances/{paper['id']}/submissions/manual",
        json={
            "idempotency_key": "m4-submit-" + uuid4().hex,
            "student_id": str(student.id), "answers": _answers(paper),
        },
    )
    assert rejected.status_code == 422


def test_queue_and_submission_view(client, db):
    _, paper = _published_paper(client, db)
    queue = client.get("/api/v1/grading/queue", params={"status": "unsubmitted"})
    assert queue.status_code == 200
    assert any(item["paper_instance_id"] == paper["id"] for item in queue.json()["items"])
    view = client.get(f"/api/v1/paper-instances/{paper['id']}/submission")
    assert view.status_code == 200
    assert view.json()["can_submit"] is True
    assert view.json()["summary"]["total_count"] == 0
