"""Photo collection is separate from recognition, submission and grading."""
import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, LargeBinary, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class AnswerPageCollection(Base):
    __tablename__ = "answer_page_collections"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    artifact_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("answer_card_artifacts.id", ondelete="RESTRICT"), unique=True)
    revision_no: Mapped[int] = mapped_column(Integer, default=0)
    effective_pages: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (CheckConstraint("revision_no >= 0", name="ck_answer_collection_revision"),)


class AnswerPageImage(Base):
    __tablename__ = "answer_page_images"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    collection_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("answer_page_collections.id", ondelete="RESTRICT"), index=True)
    image_sha256: Mapped[str] = mapped_column(String(64), index=True)
    width: Mapped[int] = mapped_column(Integer)
    height: Mapped[int] = mapped_column(Integer)
    byte_size: Mapped[int] = mapped_column(Integer)
    image_data: Mapped[bytes | None] = mapped_column(LargeBinary, deferred=True)
    retention_until: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    purged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (
        UniqueConstraint("collection_id", "image_sha256", name="uq_answer_page_image_hash"),
        CheckConstraint("width > 0 AND height > 0 AND byte_size > 0", name="ck_answer_page_image_size"),
    )


class AnswerPageEvent(Base):
    __tablename__ = "answer_page_events"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    collection_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("answer_page_collections.id", ondelete="RESTRICT"), index=True)
    revision_no: Mapped[int] = mapped_column(Integer)
    page_no: Mapped[int] = mapped_column(Integer)
    action: Mapped[str] = mapped_column(String(20))
    before_image_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("answer_page_images.id", ondelete="RESTRICT"))
    after_image_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("answer_page_images.id", ondelete="RESTRICT"))
    reviewer: Mapped[str] = mapped_column(String(100))
    reason: Mapped[str] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (
        UniqueConstraint("collection_id", "revision_no", name="uq_answer_page_event_revision"),
        CheckConstraint("revision_no > 0 AND page_no BETWEEN 1 AND 100", name="ck_answer_page_event_counts"),
    )
