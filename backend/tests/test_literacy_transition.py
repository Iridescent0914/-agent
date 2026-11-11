from datetime import datetime
from decimal import Decimal
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from app.models import Student, StudentLiteracyScore
from app.services.literacy_catalog import LITERACY_DIMENSIONS
from scripts import transition_literacy_dimensions as migration
from tests.test_literacy_scoring import db


AS_OF = datetime(2026, 11, 11, 12, tzinfo=ZoneInfo("Asia/Shanghai"))


def old_student(db, *, meaningful=False):
    student = Student(student_code="DIM-" + uuid4().hex[:12], display_name="维度迁移测试", class_name="素养测试班")
    db.add(student)
    db.flush()
    for name in [name for name in LITERACY_DIMENSIONS if name not in ("空间观念", "数据观念")] + ["数据意识"]:
        db.add(StudentLiteracyScore(
            student_id=student.id, literacy_name=name, rule_version="literacy-dynamic-v1",
            score=Decimal("70") if meaningful and name == "数据意识" else Decimal("60"),
            attempt_count=1 if meaningful and name == "数据意识" else 0,
            score_as_of_date=AS_OF.date(),
        ))
    db.flush()
    return student


def state(db, student):
    return migration.snapshot_scores(db.scalars(select(StudentLiteracyScore).where(
        StudentLiteracyScore.student_id == student.id)))


def test_transition_dry_run_can_be_rolled_back_without_changing_scores_or_history(db):
    student = old_student(db)
    before, protected = state(db, student), migration.business_fingerprints(db)
    savepoint = db.begin_nested()
    report = migration.transition(db, as_of=AS_OF, apply=False)
    assert not report["blocked_student_count"] and not report["applied"]
    row = next(item for item in report["students"] if item["student_id"] == str(student.id))
    assert len(row["before"]) == 8 and len(row["after"]) == 9
    assert report["protected_tables_unchanged"]
    savepoint.rollback()
    assert state(db, student) == before
    assert migration.business_fingerprints(db) == protected


def test_transition_blocks_the_whole_batch_when_legacy_learning_exists(db):
    healthy, legacy = old_student(db), old_student(db, meaningful=True)
    before = [state(db, value) for value in (healthy, legacy)]
    report = migration.transition(db, as_of=AS_OF, apply=True)
    assert report["blocked_student_count"] == 1 and not report["applied"]
    assert [state(db, value) for value in (healthy, legacy)] == before
    assert all("after" not in item for item in report["students"])


def test_transition_failure_rolls_back_students_already_rebuilt(db, monkeypatch):
    student = old_student(db)
    before = state(db, student)
    original = migration.persist_literacy_scores
    calls = 0

    def fail_after_first(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("test rebuild failure")
        return original(*args, **kwargs)

    monkeypatch.setattr(migration, "persist_literacy_scores", fail_after_first)
    with pytest.raises(RuntimeError, match="test rebuild failure"):
        with db.begin_nested():
            migration.transition(db, as_of=AS_OF, apply=True)
    assert state(db, student) == before
