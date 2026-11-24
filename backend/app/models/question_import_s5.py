from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class QuestionImportAuditEvent(Base):
    __tablename__ = "question_import_audit_events"
    __table_args__ = (Index("ix_import_audit_batch_item", "batch_id", "item_id", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    batch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("question_import_batches.id", ondelete="CASCADE"), nullable=False)
    item_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("question_import_items.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    decision: Mapped[str] = mapped_column(String(32), nullable=False)
    candidate_revision: Mapped[int | None] = mapped_column(Integer)
    signature: Mapped[str | None] = mapped_column(String(64))
    issue_key: Mapped[str | None] = mapped_column(String(240))
    reason: Mapped[str | None] = mapped_column(Text)
    detail: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    actor_scope: Mapped[str] = mapped_column(String(80), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
