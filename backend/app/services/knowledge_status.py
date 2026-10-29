from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Assignment, Attempt, PaperInstance, PaperInstanceItem, Question,
    StudentKnowledgeStatus,
)


CONFIRMED_STATUSES = ("auto_confirmed", "teacher_confirmed")


@dataclass(frozen=True)
class StatusEvent:
    knowledge_point_id: UUID
    status: str
    attempt_id: UUID
    revision_no: int
    answered_at: datetime


def _status_events(db: Session, student_id: UUID) -> list[StatusEvent]:
    rows = db.execute(
        select(Attempt, PaperInstanceItem, PaperInstance, Assignment, Question)
        .join(PaperInstanceItem, PaperInstanceItem.id == Attempt.paper_instance_item_id)
        .join(PaperInstance, PaperInstance.id == PaperInstanceItem.paper_instance_id)
        .join(Assignment, Assignment.id == PaperInstance.assignment_id)
        .join(Question, Question.id == Attempt.question_id)
        .where(
            Attempt.student_id == student_id,
            PaperInstance.status == "published",
            PaperInstance.invalidated_at.is_(None),
        )
        .order_by(Attempt.answered_at, Attempt.id)
    ).all()

    events: list[StatusEvent] = []
    weekly_groups: dict[tuple[UUID, UUID], list[Attempt]] = {}
    for attempt, item, paper, assignment, question in rows:
        source_module = (item.selection_audit or {}).get("source_module")
        if source_module == "history":
            if (
                attempt.grade_status not in CONFIRMED_STATUSES
                or not isinstance(attempt.is_correct, bool)
            ):
                continue
            events.append(StatusEvent(
                knowledge_point_id=question.main_knowledge_point_id,
                status="yes" if attempt.is_correct else "no",
                attempt_id=attempt.id,
                revision_no=attempt.revision_no,
                answered_at=attempt.answered_at,
            ))
        elif source_module == "weekly_summary" or assignment.assignment_type == "weekly_summary":
            weekly_groups.setdefault(
                (assignment.id, question.main_knowledge_point_id), []
            ).append(attempt)

    for (_, knowledge_point_id), attempts in weekly_groups.items():
        if any(
            item.grade_status not in CONFIRMED_STATUSES
            or not isinstance(item.is_correct, bool)
            for item in attempts
        ):
            continue
        latest = max(attempts, key=lambda item: (item.answered_at, item.id.int))
        events.append(StatusEvent(
            knowledge_point_id=knowledge_point_id,
            status="weak" if any(item.is_correct is False for item in attempts) else "yes",
            attempt_id=latest.id,
            revision_no=latest.revision_no,
            answered_at=latest.answered_at,
        ))
    return sorted(events, key=lambda item: (item.answered_at, item.attempt_id.int))


def rebuild_knowledge_statuses(db: Session, student_id: UUID) -> dict[str, int]:
    """Replay history/weekly outcomes and persist the latest label per KP."""

    latest: dict[UUID, StatusEvent] = {}
    for event in _status_events(db, student_id):
        latest[event.knowledge_point_id] = event
    existing = {
        row.knowledge_point_id: row
        for row in db.scalars(select(StudentKnowledgeStatus).where(
            StudentKnowledgeStatus.student_id == student_id
        ))
    }
    stats = {"inserted": 0, "updated": 0, "deleted": 0, "unchanged": 0}
    for knowledge_point_id, event in latest.items():
        row = existing.pop(knowledge_point_id, None)
        values = {
            "status": event.status,
            "source_attempt_id": event.attempt_id,
            "source_revision_no": event.revision_no,
        }
        if row is None:
            db.add(StudentKnowledgeStatus(
                student_id=student_id,
                knowledge_point_id=knowledge_point_id,
                **values,
            ))
            stats["inserted"] += 1
        elif any(getattr(row, key) != value for key, value in values.items()):
            for key, value in values.items():
                setattr(row, key, value)
            stats["updated"] += 1
        else:
            stats["unchanged"] += 1
    for row in existing.values():
        db.delete(row)
        stats["deleted"] += 1
    db.flush()
    return stats
