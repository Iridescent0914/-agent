from datetime import datetime
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from app.core.database import SessionLocal
from app.models import Attempt, AttemptRevision, Student, StudentLiteracyScore
from app.services.literacy_catalog import LITERACY_DIMENSIONS
from app.services.literacy_scoring import (
    effective_literacy_scores,
    LiteracyEvidence,
    load_literacy_evidence,
    literacy_score_outputs,
    parse_literacies,
    persist_literacy_scores,
    replay_literacy_scores,
)


TZ = ZoneInfo("Asia/Shanghai")


@pytest.fixture
def db():
    with SessionLocal() as session:
        try:
            yield session
        finally:
            session.rollback()


def evidence(
    day: int,
    *,
    correct: bool,
    star: int = 3,
    literacies: tuple[str, ...] = ("运算能力",),
    question_type: str = "fill_blank",
    option_count: int | None = None,
    sequence: int = 0,
) -> LiteracyEvidence:
    return LiteracyEvidence(
        attempt_id=UUID(int=day * 100 + sequence + 1),
        revision_no=1,
        answered_at=datetime(2026, 10, day, 10, sequence, tzinfo=TZ),
        question_type=question_type,
        difficulty=star,
        literacies=literacies,
        option_count=option_count,
        is_correct=correct,
    )


def test_parse_literacies_deduplicates_and_rejects_unknown_values():
    assert parse_literacies("运算能力、推理能力、运算能力") == (
        "运算能力", "推理能力",
    )
    with pytest.raises(ValueError):
        parse_literacies("待标注")


def test_replay_initializes_all_dimensions_and_caps_one_day():
    attempts = [
        evidence(28, correct=False, star=1, sequence=index)
        for index in range(5)
    ]
    result = replay_literacy_scores(
        attempts,
        as_of=datetime(2026, 10, 28, 23, 0, tzinfo=TZ),
    )
    assert set(result.states) == set(LITERACY_DIMENSIONS)
    assert result.states["运算能力"].score == pytest.approx(52)
    assert result.states["运算能力"].attempt_count == 5
    assert result.states["抽象能力"].score == 60
    assert result.states["抽象能力"].attempt_count == 0


def test_replay_equal_weights_choice_guessing_and_idle_regression():
    first = evidence(
        28,
        correct=True,
        literacies=("运算能力", "推理能力"),
        question_type="choice",
        option_count=4,
    )
    same_day = replay_literacy_scores(
        [first], as_of=datetime(2026, 10, 28, 23, 0, tzinfo=TZ)
    )
    assert same_day.states["运算能力"].score == pytest.approx(62.25)
    assert same_day.states["推理能力"].score == pytest.approx(62.25)
    next_day = replay_literacy_scores(
        [first], as_of=datetime(2026, 10, 29, 23, 0, tzinfo=TZ)
    )
    assert next_day.states["运算能力"].score == pytest.approx(62.2275)
    assert next_day.states["运算能力"].attempt_count == 1


def test_persistence_is_idempotent_for_current_demo(db):
    student = db.scalar(select(Student).where(Student.student_code == "DEMO-S001"))
    assert student is not None
    as_of = datetime(2026, 10, 31, 12, 0, tzinfo=TZ)
    loaded = load_literacy_evidence(db, student.id, as_of=as_of)
    assert loaded.evidence
    assert not loaded.invalid

    first, result = persist_literacy_scores(db, student.id, as_of=as_of)
    assert first["inserted"] + first["updated"] + first["unchanged"] == 8
    assert first["deleted"] == 0
    rows = list(db.scalars(select(StudentLiteracyScore).where(
        StudentLiteracyScore.student_id == student.id
    )))
    assert len(rows) == 8
    assert {row.literacy_name for row in rows} == set(LITERACY_DIMENSIONS)
    assert result.evidence_count == len(loaded.evidence)

    second, _ = persist_literacy_scores(db, student.id, as_of=as_of)
    assert second == {"inserted": 0, "updated": 0, "deleted": 0, "unchanged": 8}
    outputs = literacy_score_outputs(db, student.id, as_of=as_of)
    assert all(item.score_source == "computed" for item in outputs)
    assert all(item.rule_version == "literacy-dynamic-v1" for item in outputs)
    assert all(isinstance(item.score, int) for item in outputs)
    raw = effective_literacy_scores(db, student.id, as_of=as_of)
    assert any(raw[item.name] != item.score for item in outputs)

    target = loaded.evidence[0]
    attempt = db.get(Attempt, target.attempt_id)
    attempt.is_correct = not attempt.is_correct
    attempt.revision_no += 1
    db.add(AttemptRevision(
        attempt_id=attempt.id,
        revision_no=attempt.revision_no,
        before_state={"is_correct": target.is_correct},
        after_state={"is_correct": attempt.is_correct},
        operator_type="teacher",
        reason="literacy correction test",
    ))
    db.flush()
    corrected = load_literacy_evidence(db, student.id, as_of=as_of)
    corrected_target = next(
        item for item in corrected.evidence if item.attempt_id == attempt.id
    )
    assert corrected_target.revision_no == target.revision_no + 1
    assert corrected_target.is_correct is not target.is_correct
    corrected_stats, _ = persist_literacy_scores(db, student.id, as_of=as_of)
    assert corrected_stats["updated"] > 0

    attempt.grade_status = "needs_review"
    attempt.is_correct = None
    attempt.revision_no += 1
    db.add(AttemptRevision(
        attempt_id=attempt.id,
        revision_no=attempt.revision_no,
        before_state={"is_correct": corrected_target.is_correct},
        after_state={"grade_status": "needs_review", "is_correct": None},
        operator_type="teacher",
        reason="literacy reopen test",
    ))
    db.flush()
    reopened = load_literacy_evidence(db, student.id, as_of=as_of)
    assert reopened.pending_review_count >= 1
    assert attempt.id not in {item.attempt_id for item in reopened.evidence}
    reopened_stats, _ = persist_literacy_scores(db, student.id, as_of=as_of)
    assert reopened_stats["updated"] > 0


def test_empty_student_persists_eight_baseline_computed_rows(db):
    student = Student(
        student_code="LIT-" + uuid4().hex[:16],
        display_name="素养空学生",
        class_name="素养测试班",
        is_demo=True,
    )
    db.add(student)
    db.flush()
    stats, result = persist_literacy_scores(
        db,
        student.id,
        as_of=datetime(2026, 10, 31, 12, 0, tzinfo=TZ),
    )
    assert stats["inserted"] == 8
    assert result.evidence_count == 0
    assert all(state.score == 60 for state in result.states.values())
    assert all(state.attempt_count == 0 for state in result.states.values())
    outputs = literacy_score_outputs(
        db,
        student.id,
        as_of=datetime(2026, 10, 31, 12, 0, tzinfo=TZ),
    )
    assert all(item.score == 60 and item.score_source == "computed" for item in outputs)
