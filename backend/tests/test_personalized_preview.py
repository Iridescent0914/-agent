from datetime import datetime
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from app.core.database import SessionLocal
from app.models import KnowledgePoint, Question, Student
from app.services.literacy_rules import rounded_display_score
from app.services.literacy_scoring import RULE_VERSION, effective_literacy_scores
from app.services.personalized_preview import (
    personalized_question_candidates,
    preview_personalized_questions,
)
from app.services.recommendations import Candidate


TZ = ZoneInfo("Asia/Shanghai")
AS_OF = datetime(2026, 11, 2, 10, 30, tzinfo=TZ)
GRADE = "六年级上册"
CHAPTER = "math.g6.rational"
KNOWLEDGE_POINT = "math.g6.rational.kp.07_add_subtract"
LITERACY = "运算能力"


@pytest.fixture
def db():
    with SessionLocal() as session:
        try:
            yield session
        finally:
            session.rollback()


def demo_students(db, count: int = 2):
    return list(db.scalars(
        select(Student)
        .where(Student.student_code.like("DEMO-S%"), Student.archived_at.is_(None))
        .order_by(Student.student_code)
        .limit(count)
    ))


def test_database_candidates_use_exact_scope_and_readiness_contract(db):
    candidates = personalized_question_candidates(
        db,
        grade_codes=[GRADE],
        chapter_codes=[CHAPTER],
        knowledge_point_codes=[KNOWLEDGE_POINT],
        literacy_name=LITERACY,
    )

    assert candidates
    assert {item.knowledge_point_code for item in candidates} == {KNOWLEDGE_POINT}
    expected = {
        question.id: (
            question.grading_readiness == "machine_ready"
            and question.source_review_status == "reviewed"
        )
        for question in db.scalars(
            select(Question)
            .join(
                KnowledgePoint,
                KnowledgePoint.id == Question.main_knowledge_point_id,
            )
            .where(
                Question.grade == GRADE,
                Question.chapter_code == CHAPTER,
                KnowledgePoint.code == KNOWLEDGE_POINT,
                Question.archived_at.is_(None),
            )
        )
    }
    assert {item.question_id: item.ready for item in candidates} == expected


def test_preview_rounds_internal_score_once_and_remains_read_only(db, monkeypatch):
    students = demo_students(db)
    assert len(students) == 2
    score_calls = []

    def fake_scores(session, student_id, *, as_of):
        assert session is db
        score_calls.append((student_id, as_of))
        return {LITERACY: 64.4386}

    monkeypatch.setattr(
        "app.services.personalized_preview.effective_literacy_scores",
        fake_scores,
    )
    preview = preview_personalized_questions(
        db,
        student_ids=[student.id for student in students],
        grade_codes=[GRADE],
        chapter_codes=[CHAPTER],
        knowledge_point_codes=[KNOWLEDGE_POINT],
        literacy_name=LITERACY,
        as_of=AS_OF,
    )

    assert preview.as_of == AS_OF
    assert preview.score_source == "computed"
    assert preview.rule_version == RULE_VERSION
    assert score_calls == [(student.id, AS_OF) for student in students]
    assert [item.selection.literacy_score for item in preview.students] == [64, 64]
    assert [item.selection.target_stars for item in preview.students] == [
        (2, 3, 4), (2, 3, 4),
    ]
    assert not db.new
    assert not db.dirty
    assert not db.deleted


def test_batch_preview_balances_inventory_and_reports_unavoidable_reuse(
    db, monkeypatch,
):
    students = demo_students(db, count=3)
    assert len(students) == 3
    knowledge_point_id = uuid4()
    pool = [
        Candidate(
            question_id=uuid4(),
            original_qid=f"BALANCED-{difficulty}-{copy_no}",
            question_type="fill_blank",
            difficulty=difficulty,
            knowledge_point_id=knowledge_point_id,
            knowledge_point_code=KNOWLEDGE_POINT,
            knowledge_point_name="班级均衡测试知识点",
        )
        for difficulty in (2, 3, 4)
        for copy_no in (1, 2)
    ]
    monkeypatch.setattr(
        "app.services.personalized_preview.personalized_question_candidates",
        lambda *args, **kwargs: pool,
    )
    monkeypatch.setattr(
        "app.services.personalized_preview.effective_literacy_scores",
        lambda session, student_id, *, as_of: {LITERACY: 64.0},
    )

    preview = preview_personalized_questions(
        db,
        student_ids=[student.id for student in reversed(students)],
        grade_codes=[GRADE],
        chapter_codes=[CHAPTER],
        knowledge_point_codes=[KNOWLEDGE_POINT],
        literacy_name=LITERACY,
        as_of=AS_OF,
    )

    by_code = {item.student_code: item.selection for item in preview.students}
    first_ids = set(by_code[students[0].student_code].selected_question_ids)
    second_ids = set(by_code[students[1].student_code].selected_question_ids)
    third_ids = set(by_code[students[2].student_code].selected_question_ids)
    assert first_ids.isdisjoint(second_ids)
    assert third_ids == first_ids
    assert preview.reused_question_count == 3
    assert preview.max_class_usage_count == 2


def test_preview_reads_current_effective_demo_score(db):
    student = demo_students(db, count=1)[0]
    expected_score = rounded_display_score(
        effective_literacy_scores(db, student.id, as_of=AS_OF)[LITERACY]
    )

    preview = preview_personalized_questions(
        db,
        student_ids=[student.id],
        grade_codes=[GRADE],
        chapter_codes=[CHAPTER],
        knowledge_point_codes=[KNOWLEDGE_POINT],
        literacy_name=LITERACY,
        as_of=AS_OF,
    )

    assert preview.students[0].selection.literacy_score == expected_score
    assert isinstance(preview.students[0].selection.literacy_score, int)


def test_preview_applies_student_specific_exclusions(db, monkeypatch):
    student = demo_students(db, count=1)[0]
    monkeypatch.setattr(
        "app.services.personalized_preview.effective_literacy_scores",
        lambda session, student_id, *, as_of: {LITERACY: 64.0},
    )
    initial = preview_personalized_questions(
        db,
        student_ids=[student.id],
        grade_codes=[GRADE],
        chapter_codes=[CHAPTER],
        knowledge_point_codes=[KNOWLEDGE_POINT],
        literacy_name=LITERACY,
        as_of=AS_OF,
    )
    selected_ids = initial.students[0].selection.selected_question_ids
    assert selected_ids

    excluded = preview_personalized_questions(
        db,
        student_ids=[student.id],
        grade_codes=[GRADE],
        chapter_codes=[CHAPTER],
        knowledge_point_codes=[KNOWLEDGE_POINT],
        literacy_name=LITERACY,
        excluded_question_ids={student.id: {selected_ids[0]}},
        as_of=AS_OF,
    )

    assert selected_ids[0] not in excluded.students[0].selection.selected_question_ids


def test_preview_rejects_invalid_scope_before_scoring(db, monkeypatch):
    student = demo_students(db, count=1)[0]
    called = False

    def fake_scores(session, student_id, *, as_of):
        nonlocal called
        called = True
        return {LITERACY: 60.0}

    monkeypatch.setattr(
        "app.services.personalized_preview.effective_literacy_scores",
        fake_scores,
    )
    with pytest.raises(ValueError, match="主要能力不属于所选知识点"):
        preview_personalized_questions(
            db,
            student_ids=[student.id],
            grade_codes=[GRADE],
            chapter_codes=[CHAPTER],
            knowledge_point_codes=[KNOWLEDGE_POINT],
            literacy_name="几何直观",
            as_of=AS_OF,
        )
    with pytest.raises(ValueError, match="个性化知识点不属于所选章节"):
        preview_personalized_questions(
            db,
            student_ids=[student.id],
            grade_codes=[GRADE],
            chapter_codes=["math.g6.expr"],
            knowledge_point_codes=[KNOWLEDGE_POINT],
            literacy_name=LITERACY,
            as_of=AS_OF,
        )
    with pytest.raises(ValueError, match="范围外的学生"):
        preview_personalized_questions(
            db,
            student_ids=[student.id],
            grade_codes=[GRADE],
            chapter_codes=[CHAPTER],
            knowledge_point_codes=[KNOWLEDGE_POINT],
            literacy_name=LITERACY,
            excluded_question_ids={uuid4(): set()},
            as_of=AS_OF,
        )
    assert not called
