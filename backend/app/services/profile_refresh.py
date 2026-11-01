"""Synchronize all replayable student profile materializations in one transaction."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy.orm import Session

from app.services.knowledge_status import rebuild_knowledge_statuses
from app.services.literacy_scoring import persist_literacy_scores
from app.services.student_profile import load_context, persist_mastery


def refresh_student_profiles(
    db: Session,
    student_id: UUID,
    *,
    as_of: datetime | None = None,
) -> dict[str, dict[str, int]]:
    """Refresh mastery, literacy scores and knowledge labels atomically."""

    calculated_at = as_of or datetime.now(timezone.utc)
    mastery = persist_mastery(db, load_context(db, student_id))
    literacy, _ = persist_literacy_scores(
        db, student_id, as_of=calculated_at
    )
    knowledge_status = rebuild_knowledge_statuses(db, student_id)
    return {
        "mastery": mastery,
        "literacy": literacy,
        "knowledge_status": knowledge_status,
    }
