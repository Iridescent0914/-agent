"""Versioned S4 catalog checks and per-question review decisions."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class QuestionImportCatalogCheck(Base):
    __tablename__ = "question_import_catalog_checks"
    __table_args__ = (
        UniqueConstraint("item_id", "check_no", name="uq_import_catalog_item_check_no"),
        Index("ix_import_catalog_checks_batch_item", "batch_id", "item_id", "check_no"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    batch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("question_import_batches.id", ondelete="CASCADE"), nullable=False
    )
    item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("question_import_items.id", ondelete="CASCADE"), nullable=False
    )
    check_no: Mapped[int] = mapped_column(Integer, nullable=False)
    candidate_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    candidate_signature: Mapped[str] = mapped_column(String(64), nullable=False)
    exact_version: Mapped[str] = mapped_column(String(40), nullable=False)
    near_version: Mapped[str] = mapped_column(String(40), nullable=False)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class QuestionImportCatalogMatch(Base):
    __tablename__ = "question_import_catalog_matches"
    __table_args__ = (
        UniqueConstraint("check_id", "question_id", name="uq_import_catalog_check_question"),
        Index("ix_import_catalog_matches_check", "check_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    check_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("question_import_catalog_checks.id", ondelete="CASCADE"), nullable=False
    )
    question_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("questions.id", ondelete="SET NULL")
    )
    original_qid: Mapped[str] = mapped_column(String(180), nullable=False)
    catalog_review_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    catalog_signature: Mapped[str | None] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    similarity: Mapped[float | None] = mapped_column(nullable=True)
    evidence: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    decision: Mapped[str] = mapped_column(String(24), nullable=False, default="pending")
    reason: Mapped[str | None] = mapped_column(Text)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_scope: Mapped[str | None] = mapped_column(String(80))
