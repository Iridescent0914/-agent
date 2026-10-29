from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.database import SessionLocal, get_db
from app.main import app
from app.models import (
    Assignment, Attempt, AttemptRevision, KnowledgePoint, PaperInstance, PaperInstanceItem,
    Question, SchoolClass, Student,
    StudentKnowledgeStatus, Submission,
)
from app.services.knowledge_status import rebuild_knowledge_statuses
from app.services.paper_snapshots import new_item, snapshot_payload


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


def _completed_assignment_with_wrong_history(db, student):
    grouped = defaultdict(list)
    rows = db.execute(
        select(Question, KnowledgePoint)
        .join(KnowledgePoint, KnowledgePoint.id == Question.main_knowledge_point_id)
        .where(
            Question.difficulty == 3,
            Question.archived_at.is_(None),
            Question.grading_readiness == "machine_ready",
            Question.source_review_status == "reviewed",
        )
        .order_by(Question.original_qid)
    ).all()
    for question, point in rows:
        grouped[point.id].append((question, point))
    group = max(grouped.values(), key=len)
    base_question, point = group[0]
    for index in range(4 - len(group)):
        clone = Question(
            original_qid=f"test.weekly.{uuid4().hex}",
            schema_version=base_question.schema_version,
            chapter_code=base_question.chapter_code,
            chapter_name=base_question.chapter_name,
            grade=base_question.grade,
            section=base_question.section,
            question_type=base_question.question_type,
            difficulty=3,
            main_knowledge_point_id=point.id,
            question_text=f"周总结测试三星题 {index + 1}",
            options=deepcopy(base_question.options),
            correct_answer=deepcopy(base_question.correct_answer),
            reference_answer_raw=base_question.reference_answer_raw,
            answer_match_type=base_question.answer_match_type,
            answer_normalization_rule=deepcopy(base_question.answer_normalization_rule),
            solution=base_question.solution,
            wrong_analysis=base_question.wrong_analysis,
            estimated_score_rate=base_question.estimated_score_rate,
            core_literacy=base_question.core_literacy,
            is_required=False,
            has_image=base_question.has_image,
            has_equation=base_question.has_equation,
            assets=deepcopy(base_question.assets),
            source_file="weekly-summary-test",
            source_question_number=index + 1,
            source_review_status="reviewed",
            grading_readiness="machine_ready",
            import_status="parsed",
            import_issues=[],
            review_revision=1,
            review_history=[],
        )
        db.add(clone)
        group.append((clone, point))
    db.flush()
    required_question, point = group[0]
    history_question = group[1][0]
    now = datetime.now(timezone.utc)
    assignment = Assignment(
        title="周总结来源作业",
        class_id=student.class_id,
        chapter_code=required_question.chapter_code,
        teaching_scope={},
        generation_config={},
        assignment_type="practice",
    )
    db.add(assignment)
    db.flush()
    paper = PaperInstance(
        assignment_id=assignment.id,
        student_id=student.id,
        version_no=1,
        status="draft",
        recommendation_audit={},
        edit_history=[],
        edit_revision=1,
    )
    db.add(paper)
    db.flush()
    required_item = new_item(
        paper_id=paper.id,
        order_no=1,
        payload=snapshot_payload(required_question),
        selection_audit={
            "source": "recommendation",
            "source_module": "required",
            "reason_codes": ["required_question"],
        },
    )
    history_item = new_item(
        paper_id=paper.id,
        order_no=2,
        payload=snapshot_payload(history_question),
        selection_audit={
            "source": "recommendation",
            "source_module": "history",
            "reason_codes": ["history_review", "source_required_wrong"],
        },
    )
    db.add_all([required_item, history_item])
    db.flush()
    paper.status = "published"
    paper.instance_code = f"WEEK-SRC-{uuid4().hex[:8].upper()}"
    paper.published_at = now
    submission = Submission(
        student_id=student.id,
        paper_instance_id=paper.id,
        source="manual",
        idempotency_key="weekly-source-" + uuid4().hex,
        request_fingerprint=uuid4().hex,
        attachments=[],
        submitted_at=now,
    )
    db.add(submission)
    db.flush()
    for item, question, is_correct in (
        (required_item, required_question, True),
        (history_item, history_question, False),
    ):
        attempt = Attempt(
            submission_id=submission.id,
            student_id=student.id,
            paper_instance_item_id=item.id,
            question_id=question.id,
            student_answer="source-answer",
            normalized_answer="source-answer",
            grading_details={},
            grade_status="teacher_confirmed",
            is_correct=is_correct,
            manual_override=True,
            revision_no=1,
            answered_at=now,
            reviewed_at=now,
        )
        db.add(attempt)
        db.flush()
        db.add(AttemptRevision(
            attempt_id=attempt.id,
            revision_no=1,
            before_state=None,
            after_state={
                "grade_status": "teacher_confirmed",
                "is_correct": is_correct,
            },
            operator_type="teacher",
            operator_id="weekly-source-test",
            reason="prepare weekly summary source",
            created_at=now,
        ))
    db.flush()
    return assignment, point


def _isolated_student(db) -> Student:
    token = uuid4().hex
    school_class = SchoolClass(
        name=f"周总结测试班-{token}",
        grade="六年级",
        school_year="2026-2027",
    )
    db.add(school_class)
    db.flush()
    student = Student(
        student_code=f"WEEKLY-{token[:20]}",
        display_name="周总结测试学生",
        class_id=school_class.id,
        class_name=school_class.name,
        is_demo=True,
    )
    db.add(student)
    db.flush()
    return student


def test_weekly_summary_generation_shortfall_contract_and_weak_status(client, db):
    student = _isolated_student(db)
    assert student is not None and student.class_id is not None
    source, point = _completed_assignment_with_wrong_history(db, student)
    rebuild_knowledge_statuses(db, student.id)
    status = db.get(StudentKnowledgeStatus, (student.id, point.id))
    assert status is not None and status.status == "no"

    plan = {
        "class_id": str(student.class_id),
        "source_assignment_ids": [str(source.id)],
    }
    preview_response = client.post(
        "/api/v1/assignments/weekly-summary/preview", json=plan,
    )
    assert preview_response.status_code == 200, preview_response.text
    preview = preview_response.json()
    target = next(
        item for item in preview["students"] if item["student_id"] == str(student.id)
    )
    assert target["requested_count"] == 2
    assert target["selected_count"] == 2
    assert target["shortfall"] == 0
    assert target["false_knowledge_points"][0]["knowledge_point_code"] == point.code
    assert all(item["difficulty"] == 3 for item in target["items"])
    assert all(item["source_module"] == "weekly_summary" for item in target["items"])

    created_response = client.post(
        "/api/v1/assignments/weekly-summary",
        json={
            **plan,
            "title": "本周知识点总结",
            "idempotency_key": "weekly-create-" + uuid4().hex,
        },
    )
    assert created_response.status_code == 201, created_response.text
    created = created_response.json()
    assert created["assignment_type"] == "weekly_summary"
    assert created["student_count"] == 1
    paper_summary = created["papers"][0]
    assert paper_summary["requested_count"] == 2
    assert paper_summary["item_count"] == 2

    paper_response = client.get(f"/api/v1/paper-instances/{paper_summary['id']}")
    assert paper_response.status_code == 200, paper_response.text
    paper = paper_response.json()
    assert all(
        item["selection_audit"]["source_module"] == "weekly_summary"
        for item in paper["items"]
    )
    published = client.post(
        f"/api/v1/paper-instances/{paper['id']}/publish",
        json={"expected_edit_revision": paper["edit_revision"]},
    )
    assert published.status_code == 200, published.text
    published_paper = published.json()
    submitted = client.post(
        f"/api/v1/paper-instances/{paper['id']}/submissions/manual",
        json={
            "idempotency_key": "weekly-submit-" + uuid4().hex,
            "student_id": str(student.id),
            "answers": [{
                "paper_instance_item_id": item["id"],
                "order_no": item["order_no"],
                "response_status": "answered",
                "answer": item["answer_snapshot"],
            } for item in published_paper["items"]],
        },
    )
    assert submitted.status_code == 201, submitted.text
    submitted_items = submitted.json()["items"]
    first_attempt = submitted_items[0]["attempt"]
    second_attempt = db.get(Attempt, UUID(submitted_items[1]["attempt"]["id"]))
    assert second_attempt is not None
    second_attempt.grade_status = "needs_review"
    second_attempt.is_correct = None
    db.flush()
    rebuild_knowledge_statuses(db, student.id)
    status = db.get(StudentKnowledgeStatus, (student.id, point.id))
    assert status is not None and status.status == "no"

    second_attempt.grade_status = "teacher_confirmed"
    second_attempt.is_correct = True
    second_attempt.manual_override = True
    db.flush()
    rebuild_knowledge_statuses(db, student.id)
    status = db.get(StudentKnowledgeStatus, (student.id, point.id))
    assert status is not None and status.status == "yes"

    corrected = client.patch(
        f"/api/v1/attempts/{first_attempt['id']}/review",
        json={
            "action": "confirm",
            "expected_revision_no": first_attempt["revision_no"],
            "is_correct": False,
            "reason": "周总结仍答错",
            "actor": "weekly-test-teacher",
        },
    )
    assert corrected.status_code == 200, corrected.text
    db.expire_all()
    status = db.get(StudentKnowledgeStatus, (student.id, point.id))
    assert status is not None and status.status == "weak"

    profile = client.get(f"/api/v1/students/{student.id}/profile")
    assert profile.status_code == 200, profile.text
    mastery = next(
        item for item in profile.json()["masteries"]
        if item["knowledge_point_code"] == point.code
    )
    assert mastery["learning_status"] == "weak"


def test_weekly_summary_inventory_shortfall_still_creates_empty_draft(client, db):
    student = _isolated_student(db)
    assert student is not None and student.class_id is not None
    source, point = _completed_assignment_with_wrong_history(db, student)
    source_question_ids = set(db.scalars(
        select(PaperInstanceItem.question_id)
        .join(PaperInstance, PaperInstance.id == PaperInstanceItem.paper_instance_id)
        .where(PaperInstance.assignment_id == source.id)
    ))
    now = datetime.now(timezone.utc)
    for question in db.scalars(select(Question).where(
        Question.main_knowledge_point_id == point.id,
        Question.difficulty == 3,
        Question.id.not_in(source_question_ids),
    )):
        question.archived_at = now
        question.archived_by = "weekly-shortfall-test"
    db.flush()

    plan = {
        "class_id": str(student.class_id),
        "source_assignment_ids": [str(source.id)],
    }
    preview = client.post(
        "/api/v1/assignments/weekly-summary/preview", json=plan,
    )
    assert preview.status_code == 200, preview.text
    target = next(
        item for item in preview.json()["students"]
        if item["student_id"] == str(student.id)
    )
    assert target["requested_count"] == 2
    assert target["selected_count"] == 0
    assert target["shortfall"] == 2
    assert target["status"] == "insufficient_candidates"

    created = client.post(
        "/api/v1/assignments/weekly-summary",
        json={
            **plan,
            "title": "库存不足周总结",
            "idempotency_key": "weekly-shortfall-" + uuid4().hex,
        },
    )
    assert created.status_code == 201, created.text
    paper = created.json()["papers"][0]
    assert paper["status"] == "draft"
    assert paper["requested_count"] == 2
    assert paper["item_count"] == 0
    assert paper["shortfall"] == 2
