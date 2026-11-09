"""Read-only database preview for personalized question selection."""

from __future__ import annotations

from collections import Counter
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

from app.services.profile_evidence import PROFILE_QUESTION_TYPES

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import KnowledgePoint, Question, Student
from app.services.literacy_catalog import LITERACY_DIMENSIONS, parse_literacy_names
from app.services.literacy_rules import rounded_display_score
from app.services.literacy_scoring import (
    RULE_VERSION as LITERACY_RULE_VERSION,
    effective_literacy_scores,
)
from app.services.personalized_selection import (
    PersonalizedSelectionResult,
    select_personalized_questions,
)
from app.services.recommendations import Candidate


@dataclass(frozen=True)
class PersonalizedStudentPreview:
    student_id: UUID
    student_code: str
    student_name: str
    selection: PersonalizedSelectionResult


@dataclass(frozen=True)
class PersonalizedBatchPreview:
    as_of: datetime
    score_source: str
    rule_version: str
    literacy_name: str
    knowledge_point_codes: tuple[str, ...]
    candidate_count: int
    ready_candidate_count: int
    question_usage_counts: Mapping[UUID, int]
    students: tuple[PersonalizedStudentPreview, ...]

    @property
    def ready_count(self) -> int:
        return sum(item.selection.status == "ready" for item in self.students)

    @property
    def shortfall_student_count(self) -> int:
        return len(self.students) - self.ready_count

    @property
    def reused_question_count(self) -> int:
        return sum(count > 1 for count in self.question_usage_counts.values())

    @property
    def max_class_usage_count(self) -> int:
        return max(self.question_usage_counts.values(), default=1)


def _unique_nonempty(values: Sequence[str], *, field_name: str) -> tuple[str, ...]:
    normalized = tuple(values)
    if not normalized or any(
        not isinstance(value, str) or not value.strip() for value in normalized
    ):
        raise ValueError(f"{field_name}不能为空")
    if len(set(normalized)) != len(normalized):
        raise ValueError(f"{field_name}不能重复")
    return normalized


def _validate_scope(
    db: Session,
    *,
    chapter_codes: tuple[str, ...],
    knowledge_point_codes: tuple[str, ...],
    literacy_name: str,
) -> None:
    if literacy_name not in LITERACY_DIMENSIONS:
        raise ValueError("主要能力必须是正式核心素养维度")
    rows = list(db.scalars(select(KnowledgePoint).where(
        KnowledgePoint.code.in_(knowledge_point_codes),
    )))
    by_code = {row.code: row for row in rows}
    if set(by_code) != set(knowledge_point_codes):
        raise ValueError("个性化知识点不存在")
    if any(row.chapter_code not in chapter_codes for row in rows):
        raise ValueError("个性化知识点不属于所选章节")
    allowed_literacies = {
        name
        for row in rows
        for name in (row.core_competencies or [])
    }
    if literacy_name not in allowed_literacies:
        raise ValueError("主要能力不属于所选知识点")


def personalized_question_candidates(
    db: Session,
    *,
    grade_codes: Sequence[str],
    chapter_codes: Sequence[str],
    knowledge_point_codes: Sequence[str],
    literacy_name: str,
) -> list[Candidate]:
    """Load the exact teacher-selected pool, including unready diagnostics rows."""

    grades = _unique_nonempty(grade_codes, field_name="年级范围")
    chapters = _unique_nonempty(chapter_codes, field_name="章节范围")
    knowledge_codes = _unique_nonempty(
        knowledge_point_codes, field_name="个性化知识点"
    )
    _validate_scope(
        db,
        chapter_codes=chapters,
        knowledge_point_codes=knowledge_codes,
        literacy_name=literacy_name,
    )
    rows = db.execute(
        select(Question, KnowledgePoint)
        .join(KnowledgePoint, KnowledgePoint.id == Question.main_knowledge_point_id)
        .where(
            Question.grade.in_(grades),
            Question.chapter_code.in_(chapters),
            KnowledgePoint.code.in_(knowledge_codes),
            Question.archived_at.is_(None),
            Question.question_type.in_(PROFILE_QUESTION_TYPES),
        )
        .order_by(Question.original_qid, Question.id)
    ).all()
    result: list[Candidate] = []
    for question, knowledge_point in rows:
        if literacy_name not in (knowledge_point.core_competencies or []):
            continue
        try:
            question_literacies = parse_literacy_names(question.core_literacy)
        except ValueError:
            # Legacy invalid labels are not eligible for personalized selection.
            continue
        if literacy_name not in question_literacies:
            continue
        result.append(Candidate(
            question_id=question.id,
            original_qid=question.original_qid,
            question_type=question.question_type,
            difficulty=question.difficulty,
            knowledge_point_id=knowledge_point.id,
            knowledge_point_code=knowledge_point.code,
            knowledge_point_name=knowledge_point.name,
            ready=(
                question.grading_readiness == "machine_ready"
                and question.source_review_status == "reviewed"
            ),
        ))
    return result


def _students(db: Session, student_ids: Sequence[UUID]) -> list[Student]:
    ids = tuple(student_ids)
    if not ids:
        raise ValueError("至少需要一名学生")
    if len(set(ids)) != len(ids):
        raise ValueError("学生不能重复")
    found = list(db.scalars(select(Student).where(
        Student.id.in_(ids),
        Student.archived_at.is_(None),
    )))
    by_id = {student.id: student for student in found}
    if set(by_id) != set(ids):
        raise LookupError("one or more students not found")
    return [by_id[student_id] for student_id in ids]


def preview_personalized_questions(
    db: Session,
    *,
    student_ids: Sequence[UUID],
    grade_codes: Sequence[str],
    chapter_codes: Sequence[str],
    knowledge_point_codes: Sequence[str],
    literacy_name: str,
    excluded_question_ids: Mapping[UUID, Collection[UUID]] | None = None,
    as_of: datetime | None = None,
) -> PersonalizedBatchPreview:
    """Preview each student's three slots without writing or freezing a result."""

    at = as_of or datetime.now(timezone.utc)
    students = _students(db, student_ids)
    knowledge_codes = _unique_nonempty(
        knowledge_point_codes, field_name="个性化知识点"
    )
    candidates = personalized_question_candidates(
        db,
        grade_codes=grade_codes,
        chapter_codes=chapter_codes,
        knowledge_point_codes=knowledge_codes,
        literacy_name=literacy_name,
    )
    exclusions = excluded_question_ids or {}
    unknown_exclusion_students = set(exclusions) - {student.id for student in students}
    if unknown_exclusion_students:
        raise ValueError("排除题目中包含本次预览范围外的学生")

    previews_by_student: dict[UUID, PersonalizedStudentPreview] = {}
    usage_counts: Counter[UUID] = Counter()
    # Allocation order is stable even if the caller submits selected students
    # in a different order.  This makes preview/create reproducible.
    for student in sorted(
        students, key=lambda item: (item.student_code, item.id.int)
    ):
        internal_scores = effective_literacy_scores(db, student.id, as_of=at)
        public_score = rounded_display_score(internal_scores[literacy_name])
        selection = select_personalized_questions(
            student_id=student.id,
            literacy_name=literacy_name,
            literacy_score=public_score,
            knowledge_point_codes=knowledge_codes,
            candidates=candidates,
            excluded_question_ids=exclusions.get(student.id, ()),
            candidate_usage_counts=usage_counts,
        )
        usage_counts.update(selection.selected_question_ids)
        previews_by_student[student.id] = PersonalizedStudentPreview(
            student_id=student.id,
            student_code=student.student_code,
            student_name=student.display_name,
            selection=selection,
        )

    previews = [previews_by_student[student.id] for student in students]

    return PersonalizedBatchPreview(
        as_of=at,
        score_source="computed",
        rule_version=LITERACY_RULE_VERSION,
        literacy_name=literacy_name,
        knowledge_point_codes=knowledge_codes,
        candidate_count=len(candidates),
        ready_candidate_count=sum(candidate.ready for candidate in candidates),
        question_usage_counts=dict(usage_counts),
        students=tuple(previews),
    )
