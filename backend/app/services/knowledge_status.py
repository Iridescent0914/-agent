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
class AttemptFact:
    attempt: Attempt
    item: PaperInstanceItem
    assignment: Assignment
    knowledge_point_id: UUID


@dataclass(frozen=True)
class StatusEvent:
    knowledge_point_id: UUID
    status: str
    attempt_id: UUID
    revision_no: int
    answered_at: datetime


def _event_key(fact: AttemptFact) -> tuple[datetime, int]:
    return fact.attempt.answered_at, fact.attempt.id.int


def _confirmed_result(fact: AttemptFact) -> bool | None:
    attempt = fact.attempt
    if (
        attempt.grade_status not in CONFIRMED_STATUSES
        or not isinstance(attempt.is_correct, bool)
    ):
        return None
    return attempt.is_correct


def _source_module(fact: AttemptFact) -> str | None:
    value = (fact.item.selection_audit or {}).get("source_module")
    return value if isinstance(value, str) else None


def _evidence_attempt_ids(fact: AttemptFact) -> tuple[UUID, ...]:
    result: list[UUID] = []
    for value in (fact.item.selection_audit or {}).get("evidence") or []:
        try:
            attempt_id = UUID(str(value.get("attempt_id")))
        except (AttributeError, TypeError, ValueError):
            continue
        if attempt_id not in result:
            result.append(attempt_id)
    return tuple(result)


def _has_correct_between(
    facts: list[AttemptFact],
    *,
    knowledge_point_id: UUID,
    after: AttemptFact,
    before: AttemptFact,
    excluded_assignment_id: UUID | None = None,
) -> bool:
    after_key = _event_key(after)
    before_key = _event_key(before)
    return any(
        fact.knowledge_point_id == knowledge_point_id
        and fact.assignment.id != excluded_assignment_id
        and after_key < _event_key(fact) < before_key
        and _confirmed_result(fact) is True
        for fact in facts
    )


def _valid_history_source(
    fact: AttemptFact,
    *,
    by_attempt_id: dict[UUID, AttemptFact],
    facts: list[AttemptFact],
) -> AttemptFact | None:
    """Return the still-valid first wrong answer behind a history item."""

    if _source_module(fact) != "history":
        return None
    for attempt_id in _evidence_attempt_ids(fact):
        source = by_attempt_id.get(attempt_id)
        if source is None:
            continue
        if source.knowledge_point_id != fact.knowledge_point_id:
            continue
        if _source_module(source) not in {"required", "legacy_recommendation"}:
            continue
        if source.assignment.assignment_type != "practice":
            continue
        if _confirmed_result(source) is not False:
            continue
        if _event_key(source) >= _event_key(fact):
            continue
        if _has_correct_between(
            facts,
            knowledge_point_id=fact.knowledge_point_id,
            after=source,
            before=fact,
        ):
            continue
        return source
    return None


def _valid_weekly_history_source(
    group: list[AttemptFact],
    *,
    by_attempt_id: dict[UUID, AttemptFact],
    facts: list[AttemptFact],
) -> AttemptFact | None:
    """Return a valid consecutive-wrong history attempt referenced by a weekly group."""

    first_weekly = min(group, key=_event_key)
    evidence_ids = {
        attempt_id
        for fact in group
        for attempt_id in _evidence_attempt_ids(fact)
    }
    for attempt_id in sorted(evidence_ids, key=lambda value: value.int):
        history = by_attempt_id.get(attempt_id)
        if history is None or history.knowledge_point_id != first_weekly.knowledge_point_id:
            continue
        if _confirmed_result(history) is not False:
            continue
        if _valid_history_source(
            history, by_attempt_id=by_attempt_id, facts=facts,
        ) is None:
            continue
        if _event_key(history) >= _event_key(first_weekly):
            continue
        if _has_correct_between(
            facts,
            knowledge_point_id=history.knowledge_point_id,
            after=history,
            before=first_weekly,
            excluded_assignment_id=first_weekly.assignment.id,
        ):
            continue
        return history
    return None


def _status_events(db: Session, student_id: UUID) -> list[StatusEvent]:
    rows = db.execute(
        select(Attempt, PaperInstanceItem, Assignment, Question)
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
    facts = [
        AttemptFact(attempt, item, assignment, question.main_knowledge_point_id)
        for attempt, item, assignment, question in rows
    ]
    by_attempt_id = {fact.attempt.id: fact for fact in facts}

    events: list[StatusEvent] = []
    weekly_groups: dict[tuple[UUID, UUID], list[AttemptFact]] = {}
    for fact in facts:
        source_module = _source_module(fact)
        if source_module == "history":
            result = _confirmed_result(fact)
            if result is None or _valid_history_source(
                fact, by_attempt_id=by_attempt_id, facts=facts,
            ) is None:
                continue
            events.append(StatusEvent(
                knowledge_point_id=fact.knowledge_point_id,
                status="yes" if result else "no",
                attempt_id=fact.attempt.id,
                revision_no=fact.attempt.revision_no,
                answered_at=fact.attempt.answered_at,
            ))
        elif (
            source_module == "weekly_summary"
            or fact.assignment.assignment_type == "weekly_summary"
        ):
            weekly_groups.setdefault(
                (fact.assignment.id, fact.knowledge_point_id), []
            ).append(fact)

    for (_, knowledge_point_id), group in weekly_groups.items():
        results = [_confirmed_result(fact) for fact in group]
        if any(result is None for result in results):
            continue
        if _valid_weekly_history_source(
            group, by_attempt_id=by_attempt_id, facts=facts,
        ) is None:
            continue
        latest = max(group, key=_event_key)
        events.append(StatusEvent(
            knowledge_point_id=knowledge_point_id,
            status="weak" if any(result is False for result in results) else "yes",
            attempt_id=latest.attempt.id,
            revision_no=latest.attempt.revision_no,
            answered_at=latest.attempt.answered_at,
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
