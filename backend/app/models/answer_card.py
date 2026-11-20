"""Immutable, self-contained answer-card print artifacts."""
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, LargeBinary, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class AnswerCardArtifact(Base):
    __tablename__ = "answer_card_artifacts"
    __table_args__ = (
        UniqueConstraint("paper_instance_id", "revision_no", name="uq_answer_card_revision"),
        CheckConstraint("revision_no > 0 AND total_pages > 0 AND total_pages <= 100", name="ck_answer_card_counts"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    paper_instance_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("paper_instances.id", ondelete="RESTRICT"))
    revision_no: Mapped[int] = mapped_column(Integer)
    artifact_code: Mapped[str] = mapped_column(String(32), unique=True)
    dedup_key: Mapped[str] = mapped_column(String(64), unique=True)
    source_sha256: Mapped[str] = mapped_column(String(64))
    pdf_sha256: Mapped[str] = mapped_column(String(64))
    manifest_sha256: Mapped[str] = mapped_column(String(64))
    layout_version: Mapped[str] = mapped_column(String(64))
    renderer_version: Mapped[str] = mapped_column(String(100))
    total_pages: Mapped[int] = mapped_column(Integer)
    manifest: Mapped[dict[str, Any]] = mapped_column(JSONB)
    pdf_data: Mapped[bytes] = mapped_column(LargeBinary, deferred=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
