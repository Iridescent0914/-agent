from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from uuid import UUID


ALPHA = Decimal("0.2")
RECENT_WINDOW_SIZE = 5
RULE_VERSION = "m2-v1"
CONFIRMED_STATUSES = frozenset({"auto_confirmed", "teacher_confirmed"})


def quantize(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class Evidence:
    attempt_id: UUID
    student_id: UUID
    question_id: UUID
    paper_instance_item_id: UUID
    knowledge_point_id: UUID
    knowledge_point_code: str
    answered_at: datetime
    revision_no: int
    grade_status: str
    is_correct: bool | None
    question_type: str
    difficulty: int


@dataclass(frozen=True)
class HistoryPoint:
    attempt_id: UUID
    revision_no: int
    answered_at: datetime
    question_id: UUID
    paper_instance_item_id: UUID
    is_correct: bool
    mastery_before: Decimal
    mastery_after: Decimal


@dataclass(frozen=True)
class MasteryResult:
    student_id: UUID
    knowledge_point_id: UUID
    knowledge_point_code: str
    mastery: Decimal
    confidence_level: str
    attempt_count: int
    correct_count: int
    distinct_question_count: int
    recent_attempt_count: int
    recent_wrong_count: int
    last_practiced_at: datetime
    first_practiced_at: datetime
    practiced_question_types: tuple[str, ...]
    practiced_difficulties: tuple[int, ...]
    history: tuple[HistoryPoint, ...]


def evidence_problem(item: Evidence) -> str | None:
    if item.answered_at.tzinfo is None or item.answered_at.utcoffset() is None:
        return "invalid_answered_at"
    if item.revision_no < 1:
        return "invalid_revision"
    if item.grade_status == "needs_review":
        return "needs_review"
    if item.grade_status not in CONFIRMED_STATUSES:
        return "invalid_grade_status"
    if not isinstance(item.is_correct, bool):
        return "confirmed_without_result"
    return None


def confidence_level(count: int) -> str:
    return "low" if count < 3 else "medium" if count < 8 else "high"


def replay(evidence: list[Evidence]) -> dict[tuple[UUID, UUID], MasteryResult]:
    groups: dict[tuple[UUID, UUID], list[Evidence]] = {}
    seen: dict[UUID, Evidence] = {}
    for item in evidence:
        if item.attempt_id in seen:
            if seen[item.attempt_id] != item:
                raise ValueError("Conflicting current states for one Attempt")
            continue
        seen[item.attempt_id] = item
        if evidence_problem(item) is None:
            groups.setdefault((item.student_id, item.knowledge_point_id), []).append(item)

    results = {}
    for key, items in groups.items():
        items.sort(key=lambda item: (item.answered_at, item.attempt_id.int))
        mastery = Decimal("0.5")
        history = []
        for item in items:
            before = mastery
            mastery = (1 - ALPHA) * mastery + ALPHA * int(item.is_correct)
            history.append(HistoryPoint(
                attempt_id=item.attempt_id, revision_no=item.revision_no,
                answered_at=item.answered_at, question_id=item.question_id,
                paper_instance_item_id=item.paper_instance_item_id,
                is_correct=bool(item.is_correct), mastery_before=quantize(before),
                mastery_after=quantize(mastery),
            ))
        recent = items[-RECENT_WINDOW_SIZE:]
        results[key] = MasteryResult(
            student_id=key[0], knowledge_point_id=key[1],
            knowledge_point_code=items[0].knowledge_point_code,
            mastery=quantize(mastery), confidence_level=confidence_level(len(items)),
            attempt_count=len(items), correct_count=sum(item.is_correct is True for item in items),
            distinct_question_count=len({item.question_id for item in items}),
            recent_attempt_count=len(recent),
            recent_wrong_count=sum(item.is_correct is False for item in recent),
            last_practiced_at=items[-1].answered_at, first_practiced_at=items[0].answered_at,
            practiced_question_types=tuple(sorted({item.question_type for item in items})),
            practiced_difficulties=tuple(sorted({item.difficulty for item in items})),
            history=tuple(history),
        )
    return results
