"""Teacher-confirmed transcriptions, separate from immutable model outputs."""
import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class AnswerTranscription(Base):
    __tablename__ = "answer_transcriptions"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    collection_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("answer_page_collections.id", ondelete="RESTRICT"), unique=True)
    revision_no: Mapped[int] = mapped_column(Integer)
    input_sha256: Mapped[str] = mapped_column(String(64))
    evidence: Mapped[dict] = mapped_column(JSONB)
    units: Mapped[list] = mapped_column(JSONB)
    confirmed_page_nos: Mapped[list] = mapped_column(JSONB)
    submission_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("submissions.id", ondelete="RESTRICT"), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (CheckConstraint("revision_no > 0", name="ck_answer_transcription_revision"),)


class AnswerTranscriptionRevision(Base):
    __tablename__ = "answer_transcription_revisions"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    transcription_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("answer_transcriptions.id", ondelete="RESTRICT"))
    revision_no: Mapped[int] = mapped_column(Integer)
    action: Mapped[str] = mapped_column(String(16))
    state: Mapped[dict] = mapped_column(JSONB, deferred=True)
    reviewer: Mapped[str] = mapped_column(String(100))
    reason: Mapped[str] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (UniqueConstraint("transcription_id", "revision_no", name="uq_answer_transcription_revision"),)
