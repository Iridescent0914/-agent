from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.core.database import SessionLocal
from app.models import Attempt, AttemptRevision, PaperInstance, PaperInstanceItem, Student, Submission


def test_confirmed_attempt_requires_boolean_result() -> None:
    with SessionLocal() as session:
        attempt = session.scalar(select(Attempt).where(Attempt.grade_status == "teacher_confirmed"))
        assert attempt is not None
        attempt.is_correct = None
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()


def test_submission_student_must_own_paper() -> None:
    with SessionLocal() as session:
        paper = session.scalar(select(PaperInstance).limit(1))
        assert paper is not None
        other = session.scalar(select(Student).where(Student.id != paper.student_id))
        assert other is not None
        session.add(Submission(
            id=uuid4(), student_id=other.id, paper_instance_id=paper.id,
            source="manual", idempotency_key="guard-" + uuid4().hex,
            request_fingerprint="0" * 64, attachments=[], submitted_at=paper.created_at,
        ))
        with pytest.raises(DBAPIError):
            session.flush()
        session.rollback()


def test_attempt_revision_is_append_only() -> None:
    with SessionLocal() as session:
        revision = session.scalar(select(AttemptRevision).limit(1))
        assert revision is not None
        with pytest.raises(DBAPIError):
            session.execute(
                text("UPDATE attempt_revisions SET reason = reason WHERE id = :id"),
                {"id": revision.id},
            )
        session.rollback()


def test_attempt_ownership_must_match_submission_and_item() -> None:
    with SessionLocal() as session:
        submission = session.scalar(select(Submission).limit(1))
        item = session.scalar(
            select(PaperInstanceItem)
            .where(PaperInstanceItem.paper_instance_id != submission.paper_instance_id)
            .limit(1)
        )
        assert item is not None
        session.add(Attempt(
            id=uuid4(), submission_id=submission.id, student_id=submission.student_id,
            paper_instance_item_id=item.id, question_id=item.question_id,
            student_answer="A", normalized_answer="A", grading_details={},
            grade_status="auto_confirmed", is_correct=False, manual_override=False,
            revision_no=1, answered_at=submission.submitted_at,
        ))
        with pytest.raises(DBAPIError):
            session.flush()
        session.rollback()
