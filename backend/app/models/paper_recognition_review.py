"""Whole-paper quality labels, separate from transcriptions and grades."""
import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class PaperRecognitionReview(Base):
    __tablename__ = "paper_recognition_reviews"
    __table_args__ = (
        UniqueConstraint("collection_id", "recognition_sha256", name="uq_paper_recognition_review_target"),
        CheckConstraint("revision_no > 0", name="ck_paper_recognition_review_revision"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    collection_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("answer_page_collections.id", ondelete="RESTRICT"))
    recognition_sha256: Mapped[str] = mapped_column(String(64))
    revision_no: Mapped[int] = mapped_column(Integer)
    evidence: Mapped[dict] = mapped_column(JSONB)
    current: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PaperRecognitionReviewRevision(Base):
    __tablename__ = "paper_recognition_review_revisions"
    __table_args__ = (
        UniqueConstraint("review_id", "revision_no", name="uq_paper_recognition_review_revision"),
        CheckConstraint("revision_no > 0", name="ck_paper_recognition_review_history_revision"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    review_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("paper_recognition_reviews.id", ondelete="RESTRICT"))
    revision_no: Mapped[int] = mapped_column(Integer)
    data: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
