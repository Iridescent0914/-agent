from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import select

from app.core.database import SessionLocal
from app.models import Assignment, Student
from app.services.history_demo import prepare_history_demo
from app.services.paper_workflow import list_recent_completed_assignments
from app.services.recommendations import (
    assignment_is_fully_graded, history_wrong_answers,
)


@pytest.fixture
def db():
    with SessionLocal() as session:
        try:
            yield session
        finally:
            session.rollback()


def test_history_demo_is_completed_selectable_and_idempotent(db):
    prefix = "TEST-HISTORY-DEMO-" + uuid4().hex
    summaries, created = prepare_history_demo(
        db,
        seed_prefix=prefix,
        scenario_count=1,
        question_count=3,
    )
    assert created == 1
    assignment = db.scalar(select(Assignment).where(Assignment.seed_key == prefix + "-1"))
    assert assignment is not None
    assert assignment_is_fully_graded(db, assignment.id)

    students = list(db.scalars(select(Student).where(
        Student.class_id == assignment.class_id,
        Student.archived_at.is_(None),
    )))
    errors = history_wrong_answers(
        db,
        source_assignment_id=assignment.id,
        class_id=assignment.class_id,
        student_ids=[student.id for student in students],
    )
    assert any(errors.values())
    assert any(not items for items in errors.values())
    assert summaries[0]["wrong_counts"]
    recent_ids = {
        item.id for item in list_recent_completed_assignments(
            db, limit=5, class_id=assignment.class_id,
        )
    }
    assert assignment.id in recent_ids

    repeated, repeated_created = prepare_history_demo(
        db,
        seed_prefix=prefix,
        scenario_count=1,
        question_count=3,
    )
    assert repeated_created == 0
    assert repeated == summaries
