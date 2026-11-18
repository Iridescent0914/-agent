"""S1 suggestions and quality labels; no relationship writes into grading tables."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class AssessmentInput(Base):
    __tablename__ = "assessment_inputs"
    __table_args__ = (
        UniqueConstraint("paper_instance_item_id", "item_id", name="uq_assessment_input_unit"),
        CheckConstraint("revision_no > 0", name="ck_assessment_input_revision"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    paper_instance_item_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("paper_instance_items.id", ondelete="RESTRICT"))
    item_id: Mapped[str] = mapped_column(String(160))
    revision_no: Mapped[int] = mapped_column(Integer)
    # Current draft pointer. Each task retains its own immutable full snapshot.
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB)


class AssessmentTask(Base):
    __tablename__ = "assessment_tasks"
    __table_args__ = (
        CheckConstraint("status IN ('queued','running','succeeded','failed','stale','cancelled')", name="ck_assessment_task_status"),
        CheckConstraint("attempt_count >= 0 AND max_attempts > 0 AND attempt_count <= max_attempts", name="ck_assessment_task_attempts"),
        Index("ix_assessment_tasks_queue", "status", "created_at"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    input_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("assessment_inputs.id", ondelete="RESTRICT"))
    input_revision: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(24), default="equivalence")
    dedup_key: Mapped[str] = mapped_column(String(64), unique=True)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB)
    request_payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    provider: Mapped[str] = mapped_column(String(64))
    model: Mapped[str] = mapped_column(String(160))
    prompt_version: Mapped[str] = mapped_column(String(80))
    policy_version: Mapped[str] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(24), default="queued")
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer)
    timeout_seconds: Mapped[int] = mapped_column(Integer)
    lease_seconds: Mapped[int] = mapped_column(Integer)
    lease_token: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error_code: Mapped[str | None] = mapped_column(String(80))
    rerun_of: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("assessment_tasks.id", ondelete="RESTRICT"))
    rerun_reason: Mapped[str | None] = mapped_column(Text)
    action_history: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AssessmentCall(Base):
    __tablename__ = "assessment_calls"
    __table_args__ = (UniqueConstraint("task_id", "call_no", name="uq_assessment_call_no"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    task_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("assessment_tasks.id", ondelete="RESTRICT"))
    call_no: Mapped[int] = mapped_column(Integer)
    lease_token: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), unique=True)
    status: Mapped[str] = mapped_column(String(24))
    raw_response: Mapped[str | None] = mapped_column(Text)
    parsed_response: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error_code: Mapped[str | None] = mapped_column(String(80))
    usage: Mapped[dict[str, Any]] = mapped_column(JSONB)
    late_response: Mapped[bool] = mapped_column(default=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ModelQualityFeedback(Base):
    __tablename__ = "model_quality_feedback"
    __table_args__ = (
        CheckConstraint("(task_id IS NOT NULL AND recognition_item_id IS NULL) OR (task_id IS NULL AND recognition_item_id IS NOT NULL)", name="ck_quality_feedback_target"),
        CheckConstraint("revision_no > 0", name="ck_quality_feedback_revision"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    target_key: Mapped[str] = mapped_column(String(400), unique=True)
    task_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("assessment_tasks.id", ondelete="RESTRICT"))
    # Legacy re-extraction replaces RecognitionItem rows. Keep the historical ID
    # and frozen evidence without preventing that existing lifecycle.
    recognition_item_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    paper_instance_item_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("paper_instance_items.id", ondelete="RESTRICT"))
    item_id: Mapped[str] = mapped_column(String(160))
    revision_no: Mapped[int] = mapped_column(Integer)
    # Frozen model evidence, independent from subsequent manual corrections.
    evidence: Mapped[dict[str, Any]] = mapped_column(JSONB)
    current: Mapped[dict[str, Any]] = mapped_column(JSONB)


class ModelQualityFeedbackRevision(Base):
    __tablename__ = "model_quality_feedback_revisions"
    __table_args__ = (UniqueConstraint("feedback_id", "revision_no", name="uq_quality_feedback_revision"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    feedback_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("model_quality_feedback.id", ondelete="RESTRICT"))
    revision_no: Mapped[int] = mapped_column(Integer)
    data: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
