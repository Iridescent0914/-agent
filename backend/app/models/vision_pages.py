"""Page tasks deliberately do not share the equivalence/legacy recognition queue."""
import uuid
from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class VisionPageTask(Base):
    __tablename__ = "vision_page_tasks"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    collection_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("answer_page_collections.id", ondelete="RESTRICT"))
    image_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("answer_page_images.id", ondelete="RESTRICT"))
    page_no: Mapped[int] = mapped_column(Integer)
    binding_sha256: Mapped[str] = mapped_column(String(64))
    dedup_key: Mapped[str] = mapped_column(String(64), unique=True)
    generation: Mapped[int] = mapped_column(Integer)
    snapshot: Mapped[dict] = mapped_column(JSONB)
    request_payload: Mapped[dict] = mapped_column(JSONB)
    provider: Mapped[str] = mapped_column(String(64))
    model: Mapped[str] = mapped_column(String(160))
    prompt_version: Mapped[str] = mapped_column(String(80))
    policy_version: Mapped[str] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(24), default="queued")
    revision_no: Mapped[int] = mapped_column(Integer, default=1)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer)
    timeout_seconds: Mapped[int] = mapped_column(Integer)
    lease_seconds: Mapped[int] = mapped_column(Integer)
    lease_token: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    result: Mapped[dict | None] = mapped_column(JSONB)
    error_code: Mapped[str | None] = mapped_column(String(80))
    rerun_of: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("vision_page_tasks.id", ondelete="RESTRICT"))
    action_history: Mapped[list] = mapped_column(JSONB, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        CheckConstraint("status IN ('queued','running','needs_review','failed','stale','cancelled')", name="ck_vision_page_task_status"),
        CheckConstraint("page_no BETWEEN 1 AND 100 AND generation > 0 AND revision_no > 0 AND attempt_count >= 0 AND max_attempts > 0 AND attempt_count <= max_attempts AND lease_seconds > timeout_seconds AND timeout_seconds > 0", name="ck_vision_page_task_counts"),
        Index("ix_vision_page_tasks_queue", "status", "created_at"),
        Index("ix_vision_page_tasks_collection", "collection_id", "page_no"),
    )


class VisionPageCall(Base):
    __tablename__ = "vision_page_calls"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    task_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("vision_page_tasks.id", ondelete="RESTRICT"))
    call_no: Mapped[int] = mapped_column(Integer)
    lease_token: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), unique=True)
    status: Mapped[str] = mapped_column(String(24))
    raw_response: Mapped[str | None] = mapped_column(Text, deferred=True)
    parsed_response: Mapped[dict | None] = mapped_column(JSONB, deferred=True)
    error_code: Mapped[str | None] = mapped_column(String(80))
    usage: Mapped[dict] = mapped_column(JSONB)
    late_response: Mapped[bool] = mapped_column(Boolean, default=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (UniqueConstraint("task_id", "call_no", name="uq_vision_page_call_no"),)
