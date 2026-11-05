"""Replay and materialize dynamic literacy scores from current valid evidence."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Iterable
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Attempt,
    AttemptRevision,
    PaperInstance,
    PaperInstanceItem,
    Student,
    StudentLiteracyScore,
    Submission,
)
from app.schemas.profile import LiteracyScoreOut
from app.services.literacy_catalog import LITERACY_DIMENSIONS, parse_literacy_names
from app.services.literacy_rules import (
    DEFAULT_SCORE,
    cap_daily_change,
    choice_guess_probability,
    equal_literacy_weights,
    regress_score,
    rounded_display_score,
    update_with_details,
)


RULE_VERSION = "literacy-dynamic-v1"
BUSINESS_TIMEZONE = ZoneInfo("Asia/Shanghai")
_SCORE_QUANTUM = Decimal("0.0001")


@dataclass(frozen=True)
class LiteracyEvidence:
    attempt_id: UUID
    revision_no: int
    answered_at: datetime
    question_type: str
    difficulty: int
    literacies: tuple[str, ...]
    option_count: int | None
    is_correct: bool


@dataclass(frozen=True)
class InvalidLiteracyEvidence:
    attempt_id: UUID
    revision_no: int
    reason: str


@dataclass(frozen=True)
class LiteracyEvidenceSet:
    evidence: tuple[LiteracyEvidence, ...]
    invalid: tuple[InvalidLiteracyEvidence, ...]
    pending_review_count: int


@dataclass(frozen=True)
class LiteracyState:
    name: str
    score: float
    attempt_count: int
    last_practiced_at: datetime | None


@dataclass(frozen=True)
class LiteracyReplayResult:
    as_of_date: date
    states: dict[str, LiteracyState]
    evidence_count: int


def parse_literacies(value: Any) -> tuple[str, ...]:
    """Parse the immutable question snapshot's literacy label."""

    return parse_literacy_names(value)


def _option_count(value: Any) -> int | None:
    if isinstance(value, list) and len(value) >= 2:
        return len(value)
    if isinstance(value, dict) and len(value) >= 2:
        return len(value)
    return None


def _local_date(value: datetime) -> date:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(BUSINESS_TIMEZONE).date()


def _literacy_evidence_from_rows(rows: Iterable[tuple[Any, ...]]) -> LiteracyEvidenceSet:
    valid: list[LiteracyEvidence] = []
    invalid: list[InvalidLiteracyEvidence] = []
    pending = 0
    for attempt, item, paper, submission, revision in rows:
        reason: str | None = None
        snapshot = item.question_snapshot if item is not None else None
        if item is None or paper is None or submission is None:
            reason = "missing_ownership_record"
        elif (
            attempt.student_id != submission.student_id
            or attempt.student_id != paper.student_id
            or submission.paper_instance_id != paper.id
            or attempt.question_id != item.question_id
        ):
            reason = "ownership_mismatch"
        elif paper.status != "published":
            reason = "unpublished_paper"
        elif paper.invalidated_at is not None:
            # An invalidated paper is valid historical/audit data, not broken
            # evidence. Keep it out of the current score without failing a
            # profile rebuild.
            continue
        elif not isinstance(snapshot, dict):
            reason = "invalid_question_snapshot"
        elif revision is None:
            reason = "missing_current_revision"
        elif attempt.grade_status == "needs_review":
            pending += 1
            continue
        elif attempt.grade_status not in {"auto_confirmed", "teacher_confirmed"}:
            reason = "invalid_grade_status"
        elif type(attempt.is_correct) is not bool:
            reason = "missing_confirmed_result"
        elif snapshot.get("question_type") not in {"choice", "fill_blank", "true_false"}:
            reason = "unsupported_question_type"
        elif type(snapshot.get("difficulty")) is not int or not 1 <= snapshot["difficulty"] <= 5:
            reason = "invalid_difficulty"
        else:
            try:
                literacies = parse_literacies(snapshot.get("core_literacy"))
            except ValueError:
                reason = "invalid_core_literacy"
            else:
                valid.append(LiteracyEvidence(
                    attempt_id=attempt.id,
                    revision_no=attempt.revision_no,
                    answered_at=attempt.answered_at,
                    question_type=snapshot["question_type"],
                    difficulty=snapshot["difficulty"],
                    literacies=literacies,
                    option_count=_option_count(item.options_snapshot),
                    is_correct=attempt.is_correct,
                ))
        if reason is not None:
            invalid.append(InvalidLiteracyEvidence(
                attempt_id=attempt.id,
                revision_no=attempt.revision_no,
                reason=reason,
            ))
    return LiteracyEvidenceSet(tuple(valid), tuple(invalid), pending)


def load_literacy_evidence_many(
    db: Session,
    student_ids: Iterable[UUID],
    *,
    as_of: datetime,
) -> dict[UUID, LiteracyEvidenceSet]:
    """Load and validate current attempt evidence for multiple students."""

    ids = list(dict.fromkeys(student_ids))
    if not ids:
        return {}
    existing_ids = set(db.scalars(
        select(Student.id).where(
            Student.id.in_(ids), Student.archived_at.is_(None)
        )
    ))
    if existing_ids != set(ids):
        raise LookupError("one or more students not found")

    grouped: dict[UUID, list[tuple[Any, ...]]] = {
        student_id: [] for student_id in ids
    }
    rows = db.execute(
        select(Attempt, PaperInstanceItem, PaperInstance, Submission, AttemptRevision)
        .outerjoin(PaperInstanceItem, PaperInstanceItem.id == Attempt.paper_instance_item_id)
        .outerjoin(PaperInstance, PaperInstance.id == PaperInstanceItem.paper_instance_id)
        .outerjoin(Submission, Submission.id == Attempt.submission_id)
        .outerjoin(
            AttemptRevision,
            (AttemptRevision.attempt_id == Attempt.id)
            & (AttemptRevision.revision_no == Attempt.revision_no),
        )
        .where(Attempt.student_id.in_(ids), Attempt.answered_at <= as_of)
        .order_by(Attempt.student_id, Attempt.answered_at, Attempt.id)
    ).all()
    for row in rows:
        grouped[row[0].student_id].append(row)
    return {
        student_id: _literacy_evidence_from_rows(grouped[student_id])
        for student_id in ids
    }


def load_literacy_evidence(
    db: Session,
    student_id: UUID,
    *,
    as_of: datetime,
) -> LiteracyEvidenceSet:
    """Load current Attempt states and validate immutable ownership snapshots."""

    try:
        return load_literacy_evidence_many(
            db, [student_id], as_of=as_of
        )[student_id]
    except LookupError as exc:
        raise LookupError("student not found") from exc


def replay_literacy_scores(
    evidence: Iterable[LiteracyEvidence],
    *,
    as_of: datetime,
) -> LiteracyReplayResult:
    """Replay evidence by Shanghai natural day with caps and idle regression."""

    as_of_date = _local_date(as_of)
    ordered = sorted(
        (item for item in evidence if item.answered_at <= as_of),
        key=lambda item: (item.answered_at, str(item.attempt_id)),
    )
    by_day: dict[date, list[LiteracyEvidence]] = defaultdict(list)
    for item in ordered:
        by_day[_local_date(item.answered_at)].append(item)

    scores = {name: DEFAULT_SCORE for name in LITERACY_DIMENSIONS}
    counts = {name: 0 for name in LITERACY_DIMENSIONS}
    last_practiced: dict[str, datetime | None] = {
        name: None for name in LITERACY_DIMENSIONS
    }
    if ordered:
        current_day = min(by_day)
        while current_day <= as_of_date:
            events = by_day.get(current_day, [])
            practiced = {
                literacy
                for item in events
                for literacy in item.literacies
            }
            day_start = {name: scores[name] for name in practiced}
            for item in events:
                weights = equal_literacy_weights(item.literacies)
                guess = choice_guess_probability(item.option_count)
                for name, weight in weights.items():
                    update = update_with_details(
                        scores[name],
                        item.difficulty,
                        item.is_correct,
                        counts[name],
                        weight=weight,
                        choice=item.question_type == "choice",
                        guess_probability=guess,
                    )
                    scores[name] = update.score_after
                    counts[name] += 1
                    last_practiced[name] = item.answered_at
            for name in LITERACY_DIMENSIONS:
                if name in practiced:
                    scores[name] = cap_daily_change(day_start[name], scores[name])
                else:
                    scores[name] = regress_score(scores[name])
            current_day += timedelta(days=1)

    states = {
        name: LiteracyState(
            name=name,
            score=scores[name],
            attempt_count=counts[name],
            last_practiced_at=last_practiced[name],
        )
        for name in LITERACY_DIMENSIONS
    }
    return LiteracyReplayResult(as_of_date, states, len(ordered))


def _decimal_score(value: float) -> Decimal:
    return Decimal(str(value)).quantize(_SCORE_QUANTUM, rounding=ROUND_HALF_UP)


def literacy_outputs_from_scores(scores: dict[str, float]) -> list[LiteracyScoreOut]:
    """Convert internal decimal scores to the half-up rounded public contract."""

    return [
        LiteracyScoreOut(
            name=name,
            score=rounded_display_score(scores.get(name, DEFAULT_SCORE)),
            score_source="computed",
            rule_version=RULE_VERSION,
        )
        for name in LITERACY_DIMENSIONS
    ]


def default_computed_literacies() -> list[LiteracyScoreOut]:
    """Return initialized computed values for a student with no evidence."""

    return literacy_outputs_from_scores({})


def _effective_materialized_scores(
    rows: list[StudentLiteracyScore],
    *,
    target_date: date,
) -> dict[str, float] | None:
    """Read a complete materialized score set and regress it to ``target_date``."""

    by_name = {row.literacy_name: row for row in rows}
    complete = (
        set(by_name) == set(LITERACY_DIMENSIONS)
        and all(row.rule_version == RULE_VERSION for row in rows)
        and all(row.score_as_of_date <= target_date for row in rows)
    )
    if not complete:
        return None

    scores: dict[str, float] = {}
    for name in LITERACY_DIMENSIONS:
        row = by_name[name]
        value = float(row.score)
        for _ in range((target_date - row.score_as_of_date).days):
            value = regress_score(value)
        scores[name] = value
    return scores


def effective_literacy_scores_many(
    db: Session,
    student_ids: Iterable[UUID],
    *,
    as_of: datetime,
) -> dict[UUID, dict[str, float]]:
    """Return effective scores for multiple students with one materialized-row query.

    Students whose materialization is missing, stale, or from another rule version
    fall back to one batch evidence query and are replayed independently in memory.
    """

    ids = list(dict.fromkeys(student_ids))
    if not ids:
        return {}

    grouped: dict[UUID, list[StudentLiteracyScore]] = {
        student_id: [] for student_id in ids
    }
    for row in db.scalars(
        select(StudentLiteracyScore).where(
            StudentLiteracyScore.student_id.in_(ids)
        )
    ):
        grouped[row.student_id].append(row)

    target_date = _local_date(as_of)
    result: dict[UUID, dict[str, float]] = {}
    fallback_ids: list[UUID] = []
    for student_id in ids:
        scores = _effective_materialized_scores(
            grouped[student_id], target_date=target_date
        )
        if scores is None:
            fallback_ids.append(student_id)
        else:
            result[student_id] = scores

    fallback_evidence = load_literacy_evidence_many(
        db, fallback_ids, as_of=as_of
    )
    for student_id in fallback_ids:
        loaded = fallback_evidence[student_id]
        if loaded.invalid:
            raise ValueError(
                "Cannot compute literacy profile: "
                f"{len(loaded.invalid)} invalid evidence records"
            )
        replayed = replay_literacy_scores(loaded.evidence, as_of=as_of)
        result[student_id] = {
            name: state.score for name, state in replayed.states.items()
        }
    return result


def effective_literacy_scores(
    db: Session,
    student_id: UUID,
    *,
    as_of: datetime,
) -> dict[str, float]:
    """Return internal decimal scores effective at ``as_of`` without writing."""

    return effective_literacy_scores_many(
        db, [student_id], as_of=as_of
    )[student_id]


def literacy_score_outputs(
    db: Session,
    student_id: UUID,
    *,
    as_of: datetime,
) -> list[LiteracyScoreOut]:
    """Return the rounded public score contract for profile and analytics APIs."""

    return literacy_outputs_from_scores(
        effective_literacy_scores(db, student_id, as_of=as_of)
    )


def persist_literacy_scores(
    db: Session,
    student_id: UUID,
    *,
    as_of: datetime,
) -> tuple[dict[str, int], LiteracyReplayResult]:
    """Replay and idempotently synchronize all eight materialized rows."""

    loaded = load_literacy_evidence(db, student_id, as_of=as_of)
    if loaded.invalid:
        reasons: dict[str, int] = defaultdict(int)
        for item in loaded.invalid:
            reasons[item.reason] += 1
        summary = ", ".join(f"{key}={value}" for key, value in sorted(reasons.items()))
        raise ValueError(
            f"Cannot rebuild literacy scores: {len(loaded.invalid)} invalid evidence records ({summary})"
        )
    replayed = replay_literacy_scores(loaded.evidence, as_of=as_of)
    existing = {
        row.literacy_name: row
        for row in db.scalars(select(StudentLiteracyScore).where(
            StudentLiteracyScore.student_id == student_id
        ))
    }
    stats = {"inserted": 0, "updated": 0, "deleted": 0, "unchanged": 0}
    for name in LITERACY_DIMENSIONS:
        state = replayed.states[name]
        values = {
            "score": _decimal_score(state.score),
            "attempt_count": state.attempt_count,
            "last_practiced_at": state.last_practiced_at,
            "score_as_of_date": replayed.as_of_date,
            "rule_version": RULE_VERSION,
        }
        row = existing.pop(name, None)
        if row is None:
            db.add(StudentLiteracyScore(
                student_id=student_id,
                literacy_name=name,
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
    return stats, replayed
