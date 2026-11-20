from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
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
    mastery_level: Mapped[str | None] = mapped_column(String(16))
    core_competencies: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    curriculum_basis: Mapped[str | None] = mapped_column(Text)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("knowledge_points.id", ondelete="SET NULL")
    )

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
    grade: Mapped[str] = mapped_column(String(80), nullable=False, default="未设置")
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
    core_literacy: Mapped[str] = mapped_column(String(255), nullable=False, default="待标注")
    is_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    has_image: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    has_equation: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    assets: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    source_file: Mapped[str] = mapped_column(Text, nullable=False)
    source_question_number: Mapped[int] = mapped_column(Integer, nullable=False)
    content_fingerprint: Mapped[str | None] = mapped_column(String(64), index=True)
    exact_fingerprint_v2: Mapped[str | None] = mapped_column(String(64), index=True)
    import_batch_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("question_import_batches.id", ondelete="SET NULL"), index=True
    )
    import_item_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("question_import_items.id", ondelete="SET NULL"), index=True
    )
    source_review_status: Mapped[str] = mapped_column(String(32), nullable=False)
    grading_readiness: Mapped[str] = mapped_column(String(32), nullable=False)
    import_status: Mapped[str] = mapped_column(String(32), nullable=False, default="parsed")
    import_issues: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    review_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    review_history: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    reviewed_by: Mapped[str | None] = mapped_column(String(80))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    archived_by: Mapped[str | None] = mapped_column(String(80))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    main_knowledge_point: Mapped[KnowledgePoint] = relationship()


class QuestionImportBatch(TimestampMixin, Base):
    __tablename__ = "question_import_batches"
    __table_args__ = (
        Index("uq_question_import_active_request", "owner_scope", "request_fingerprint",
              unique=True, postgresql_where=text("files_purged_at IS NULL")),
        Index("ix_question_import_batch_lease", "status", "lease_until"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_scope: Mapped[str] = mapped_column(String(80), nullable=False, default="internal-demo")
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    catalog_snapshot_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    source_mode: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="uploaded")
    stage: Mapped[str] = mapped_column(String(40), nullable=False, default="files_saved")
    files: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    checkpoint: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    error_code: Mapped[str | None] = mapped_column(String(80))
    error_message: Mapped[str | None] = mapped_column(Text)
    worker_token: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    files_purged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class QuestionImportItem(TimestampMixin, Base):
    __tablename__ = "question_import_items"
    __table_args__ = (UniqueConstraint("batch_id", "ordinal", name="uq_question_import_item_ordinal"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    batch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("question_import_batches.id", ondelete="CASCADE"), index=True
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    is_fake: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class SchoolClass(TimestampMixin, Base):
    __tablename__ = "school_classes"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    grade: Mapped[str] = mapped_column(String(40), nullable=False, default="未设置")
    school_year: Mapped[str] = mapped_column(String(40), nullable=False, default="未设置")
    teacher_name: Mapped[str | None] = mapped_column(String(80))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    students: Mapped[list[Student]] = relationship(back_populates="school_class")


class Student(TimestampMixin, Base):
    __tablename__ = "students"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    student_code: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(80), nullable=False)
    class_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("school_classes.id", ondelete="SET NULL"), index=True
    )
    class_name: Mapped[str] = mapped_column(String(120), nullable=False)
    is_demo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    archived_by: Mapped[str | None] = mapped_column(String(80))

    school_class: Mapped[SchoolClass | None] = relationship(back_populates="students")


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
    knowledge_point_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("knowledge_points.id", ondelete="CASCADE"), primary_key=True
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


class StudentLiteracyScore(Base):
    __tablename__ = "student_literacy_scores"
    __table_args__ = (
        CheckConstraint("score BETWEEN 0 AND 100", name="ck_literacy_score_range"),
        CheckConstraint("attempt_count >= 0", name="ck_literacy_attempt_count_nonnegative"),
    )

    student_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("students.id", ondelete="CASCADE"), primary_key=True
    )
    literacy_name: Mapped[str] = mapped_column(String(40), primary_key=True)
    score: Mapped[Decimal] = mapped_column(Numeric(7, 4), nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_practiced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    score_as_of_date: Mapped[date] = mapped_column(Date, nullable=False)
    rule_version: Mapped[str] = mapped_column(String(64), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class StudentKnowledgeStatus(Base):
    __tablename__ = "student_knowledge_statuses"
    __table_args__ = (
        CheckConstraint("status IN ('yes', 'no', 'weak')", name="ck_student_knowledge_status"),
        CheckConstraint("source_revision_no >= 1", name="ck_student_knowledge_status_revision"),
    )

    student_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("students.id", ondelete="CASCADE"), primary_key=True
    )
    knowledge_point_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("knowledge_points.id", ondelete="CASCADE"), primary_key=True
    )
    status: Mapped[str] = mapped_column(String(8), nullable=False)
    source_attempt_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("attempts.id", ondelete="RESTRICT"), nullable=False
    )
    source_revision_no: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class Assignment(TimestampMixin, Base):
    __tablename__ = "assignments"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    seed_key: Mapped[str | None] = mapped_column(String(100), unique=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(100), unique=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    class_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("school_classes.id", ondelete="RESTRICT"), index=True
    )
    chapter_code: Mapped[str] = mapped_column(String(160), nullable=False)
    teaching_scope: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    generation_config: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    assignment_type: Mapped[str] = mapped_column(String(32), nullable=False, default="practice")

    school_class: Mapped[SchoolClass | None] = relationship()


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
    invalidated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    invalidation_type: Mapped[str | None] = mapped_column(String(24))
    invalidation_reason: Mapped[str | None] = mapped_column(Text)
    invalidated_by: Mapped[str | None] = mapped_column(String(80))
    superseded_by_instance_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey('paper_instances.id', ondelete='RESTRICT'), unique=True
    )


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
    knowledge_point_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
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
    request_fingerprint: Mapped[str | None] = mapped_column(String(64))
    attachments: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class RecognitionRun(TimestampMixin, Base):
    __tablename__ = "recognition_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('uploaded', 'processing', 'ready_for_review', 'failed', 'committed', 'discarded')",
            name="ck_recognition_run_status",
        ),
        CheckConstraint("byte_size > 0", name="ck_recognition_run_byte_size"),
        CheckConstraint(
            "pixel_width > 0 AND pixel_height > 0",
            name="ck_recognition_run_dimensions",
        ),
        Index("ix_recognition_runs_paper_status", "paper_instance_id", "status"),
        Index(
            "uq_recognition_run_active_per_paper",
            "paper_instance_id",
            unique=True,
            postgresql_where=text(
                "status IN ('uploaded', 'processing', 'ready_for_review', 'failed')"
            ),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    paper_instance_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("paper_instances.id", ondelete="RESTRICT"), nullable=False
    )
    student_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("students.id", ondelete="RESTRICT"), nullable=False
    )
    submission_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("submissions.id", ondelete="RESTRICT"), unique=True
    )
    idempotency_key: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str] = mapped_column(String(160), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(80), nullable=False)
    image_storage_key: Mapped[str] = mapped_column(String(500), nullable=False)
    image_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    original_filename: Mapped[str | None] = mapped_column(String(255))
    content_type: Mapped[str] = mapped_column(String(80), nullable=False)
    byte_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    pixel_width: Mapped[int] = mapped_column(Integer, nullable=False)
    pixel_height: Mapped[int] = mapped_column(Integer, nullable=False)
    raw_response: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error_code: Mapped[str | None] = mapped_column(String(80))
    error_message: Mapped[str | None] = mapped_column(Text)
    processing_token: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    committed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retention_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    image_purged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RecognitionItem(TimestampMixin, Base):
    __tablename__ = "recognition_items"
    __table_args__ = (
        UniqueConstraint("recognition_run_id", "paper_instance_item_id", name="uq_recognition_run_item"),
        UniqueConstraint("recognition_run_id", "order_no", name="uq_recognition_run_order"),
        CheckConstraint("order_no > 0", name="ck_recognition_item_order"),
        CheckConstraint(
            "recognition_status IN ('readable', 'blank', 'missing', 'illegible', 'ambiguous')",
            name="ck_recognition_item_status",
        ),
        CheckConstraint(
            "confidence IS NULL OR (confidence BETWEEN 0 AND 1)",
            name="ck_recognition_item_confidence",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    recognition_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("recognition_runs.id", ondelete="CASCADE"), nullable=False
    )
    paper_instance_item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("paper_instance_items.id", ondelete="RESTRICT"), nullable=False
    )
    order_no: Mapped[int] = mapped_column(Integer, nullable=False)
    recognition_status: Mapped[str] = mapped_column(String(24), nullable=False)
    recognized_answer: Mapped[Any | None] = mapped_column(JSONB)
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    provider_payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


class Attempt(Base):
    __tablename__ = "attempts"
    __table_args__ = (
        CheckConstraint(
            "grade_status IN ('auto_confirmed', 'needs_review', 'teacher_confirmed')",
            name="ck_attempt_grade_status",
        ),
        CheckConstraint(
            "(grade_status = 'needs_review' AND is_correct IS NULL) OR "
            "(grade_status IN ('auto_confirmed', 'teacher_confirmed') AND is_correct IS NOT NULL)",
            name="ck_attempt_result_complete",
        ),
        CheckConstraint(
            "ocr_confidence IS NULL OR (ocr_confidence BETWEEN 0 AND 1)",
            name="ck_attempt_ocr_confidence_range",
        ),
        CheckConstraint(
            "NOT manual_override OR grade_status IN ('teacher_confirmed', 'needs_review')",
            name="ck_attempt_manual_override_status",
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
    grading_details: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
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
    operator_id: Mapped[str | None] = mapped_column(String(80))
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
