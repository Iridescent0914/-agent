from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class KnowledgePoint(Base):
    __tablename__ = "knowledge_points"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    code: Mapped[str] = mapped_column(String(160), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    chapter_code: Mapped[str] = mapped_column(String(160), nullable=False)
    chapter_name: Mapped[str] = mapped_column(String(160), nullable=False)
    section: Mapped[str | None] = mapped_column(String(255))
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("knowledge_points.id", ondelete="SET NULL")
    )

    exam_points: Mapped[list[ExamPoint]] = relationship(back_populates="knowledge_point")


class ExamPoint(Base):
    __tablename__ = "exam_points"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    code: Mapped[str] = mapped_column(String(180), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(500), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    knowledge_point_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("knowledge_points.id", ondelete="RESTRICT"), nullable=False
    )

    knowledge_point: Mapped[KnowledgePoint] = relationship(back_populates="exam_points")


class Question(TimestampMixin, Base):
    __tablename__ = "questions"
    __table_args__ = (
        CheckConstraint("difficulty BETWEEN 1 AND 5", name="ck_questions_difficulty"),
        CheckConstraint(
            "question_type IN ('choice', 'fill_blank', 'calculation', 'solution', 'true_false', 'unknown')",
            name="ck_questions_type",
        ),
        CheckConstraint(
            "grading_readiness <> 'machine_ready' OR "
            "(correct_answer IS NOT NULL AND answer_match_type IS NOT NULL)",
            name="ck_machine_ready_answer",
        ),
        Index("ix_questions_filters", "chapter_code", "question_type", "difficulty"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    original_qid: Mapped[str] = mapped_column(String(180), unique=True, nullable=False)
    schema_version: Mapped[str] = mapped_column(String(32), nullable=False)
    chapter_code: Mapped[str] = mapped_column(String(160), nullable=False)
    chapter_name: Mapped[str] = mapped_column(String(160), nullable=False)
    section: Mapped[str] = mapped_column(String(255), nullable=False)
    question_type: Mapped[str] = mapped_column(String(32), nullable=False)
    difficulty: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    main_knowledge_point_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("knowledge_points.id", ondelete="RESTRICT"), nullable=False
    )
    question_text: Mapped[str] = mapped_column(Text, nullable=False)
    options: Mapped[Any | None] = mapped_column(JSONB)
    correct_answer: Mapped[Any | None] = mapped_column(JSONB)
    reference_answer_raw: Mapped[str] = mapped_column(Text, nullable=False)
    answer_match_type: Mapped[str | None] = mapped_column(String(32))
    answer_normalization_rule: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    solution: Mapped[str | None] = mapped_column(Text)
    wrong_analysis: Mapped[str | None] = mapped_column(Text)
    estimated_score_rate: Mapped[str | None] = mapped_column(String(64))
    has_image: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    has_equation: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    assets: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    source_file: Mapped[str] = mapped_column(Text, nullable=False)
    source_question_number: Mapped[int] = mapped_column(Integer, nullable=False)
    source_review_status: Mapped[str] = mapped_column(String(32), nullable=False)
    grading_readiness: Mapped[str] = mapped_column(String(32), nullable=False)
    import_status: Mapped[str] = mapped_column(String(32), nullable=False, default="parsed")
    import_issues: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    review_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    review_history: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    reviewed_by: Mapped[str | None] = mapped_column(String(80))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    main_knowledge_point: Mapped[KnowledgePoint] = relationship()
    exam_point_links: Mapped[list[QuestionExamPoint]] = relationship(
        back_populates="question", cascade="all, delete-orphan"
    )


class QuestionExamPoint(Base):
    __tablename__ = "question_exam_points"
    __table_args__ = (
        CheckConstraint("relation_type IN ('main', 'related')", name="ck_qep_relation_type"),
        Index(
            "uq_question_one_main_exam_point",
            "question_id",
            unique=True,
            postgresql_where=text("relation_type = 'main'"),
        ),
        Index("ix_qep_exam_question", "exam_point_id", "question_id"),
    )

    question_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("questions.id", ondelete="CASCADE"), primary_key=True
    )
    exam_point_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("exam_points.id", ondelete="RESTRICT"), primary_key=True
    )
    relation_type: Mapped[str] = mapped_column(String(16), nullable=False)

    question: Mapped[Question] = relationship(back_populates="exam_point_links")
    exam_point: Mapped[ExamPoint] = relationship()


class Student(TimestampMixin, Base):
    __tablename__ = "students"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    student_code: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(80), nullable=False)
    class_name: Mapped[str] = mapped_column(String(120), nullable=False)
    is_demo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class StudentMastery(Base):
    __tablename__ = "student_masteries"
    __table_args__ = (
        CheckConstraint("mastery BETWEEN 0 AND 1", name="ck_mastery_range"),
        CheckConstraint(
            "confidence_level IN ('low', 'medium', 'high')", name="ck_confidence_level"
        ),
        CheckConstraint(
            "attempt_count >= 0 AND correct_count >= 0 AND recent_wrong_count >= 0",
            name="ck_mastery_counts_nonnegative",
        ),
    )

    student_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("students.id", ondelete="CASCADE"), primary_key=True
    )
    exam_point_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("exam_points.id", ondelete="CASCADE"), primary_key=True
    )
    mastery: Mapped[Decimal] = mapped_column(Numeric(5, 4), nullable=False)
    confidence_level: Mapped[str] = mapped_column(String(16), nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    correct_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    recent_wrong_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_practiced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class Assignment(TimestampMixin, Base):
    __tablename__ = "assignments"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    seed_key: Mapped[str | None] = mapped_column(String(100), unique=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(100), unique=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    chapter_code: Mapped[str] = mapped_column(String(160), nullable=False)
    teaching_scope: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    generation_config: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    assignment_type: Mapped[str] = mapped_column(String(32), nullable=False, default="practice")


class PaperInstance(TimestampMixin, Base):
    __tablename__ = "paper_instances"
    __table_args__ = (
        UniqueConstraint(
            "assignment_id", "student_id", "version_no", name="uq_paper_assignment_student_version"
        ),
        CheckConstraint("version_no > 0", name="ck_paper_version_positive"),
        CheckConstraint("status IN ('draft', 'published')", name="ck_paper_status"),
        CheckConstraint("edit_revision > 0", name="ck_paper_edit_revision_positive"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    assignment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("assignments.id", ondelete="RESTRICT"), nullable=False
    )
    student_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("students.id", ondelete="RESTRICT"), nullable=False
    )
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="draft")
    instance_code: Mapped[str | None] = mapped_column(String(64), unique=True)
    supersedes_instance_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("paper_instances.id", ondelete="SET NULL")
    )
    recommendation_audit: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    edit_history: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    edit_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PaperInstanceItem(TimestampMixin, Base):
    __tablename__ = "paper_instance_items"
    __table_args__ = (
        UniqueConstraint("paper_instance_id", "order_no", name="uq_paper_item_order"),
        CheckConstraint("order_no > 0", name="ck_paper_item_order_positive"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    paper_instance_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("paper_instances.id", ondelete="CASCADE"), nullable=False
    )
    question_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("questions.id", ondelete="RESTRICT"), nullable=False
    )
    order_no: Mapped[int] = mapped_column(Integer, nullable=False)
    question_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    options_snapshot: Mapped[Any | None] = mapped_column(JSONB)
    answer_snapshot: Mapped[Any] = mapped_column(JSONB, nullable=False)
    answer_rule_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    exam_point_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    answer_region: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    selection_audit: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


class Submission(Base):
    __tablename__ = "submissions"
    __table_args__ = (
        CheckConstraint("source IN ('manual', 'image')", name="ck_submission_source"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    student_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("students.id", ondelete="RESTRICT"), nullable=False
    )
    paper_instance_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("paper_instances.id", ondelete="RESTRICT"),
        unique=True, nullable=False
    )
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    attachments: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Attempt(Base):
    __tablename__ = "attempts"
    __table_args__ = (
        CheckConstraint(
            "grade_status IN ('auto_confirmed', 'needs_review', 'teacher_confirmed')",
            name="ck_attempt_grade_status",
        ),
        CheckConstraint(
            "grade_status <> 'needs_review' OR is_correct IS NULL",
            name="ck_needs_review_unknown",
        ),
        CheckConstraint("revision_no > 0", name="ck_attempt_revision_positive"),
        Index("ix_attempts_student_answered", "student_id", "answered_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    submission_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("submissions.id", ondelete="CASCADE"), nullable=False
    )
    student_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("students.id", ondelete="RESTRICT"), nullable=False
    )
    paper_instance_item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("paper_instance_items.id", ondelete="RESTRICT"),
        unique=True, nullable=False
    )
    question_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("questions.id", ondelete="RESTRICT"), nullable=False
    )
    student_answer: Mapped[Any | None] = mapped_column(JSONB)
    normalized_answer: Mapped[Any | None] = mapped_column(JSONB)
    grade_status: Mapped[str] = mapped_column(String(24), nullable=False)
    is_correct: Mapped[bool | None] = mapped_column(Boolean)
    ocr_confidence: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    recognition_status: Mapped[str | None] = mapped_column(String(24))
    manual_override: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    revision_no: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    answered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AttemptRevision(Base):
    __tablename__ = "attempt_revisions"
    __table_args__ = (
        UniqueConstraint("attempt_id", "revision_no", name="uq_attempt_revision"),
        CheckConstraint("revision_no > 0", name="ck_attempt_revision_log_positive"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    attempt_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("attempts.id", ondelete="CASCADE"), nullable=False
    )
    revision_no: Mapped[int] = mapped_column(Integer, nullable=False)
    before_state: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    after_state: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    operator_type: Mapped[str] = mapped_column(String(32), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
