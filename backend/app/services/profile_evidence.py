"""Shared eligibility rules for current profiles and student statistics.

Published snapshots describe historical questions. Current catalog edits and
archiving must not change ownership or eligibility of historical attempts.
"""
from __future__ import annotations

from collections.abc import Collection, Iterable
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Attempt, AttemptRevision, PaperInstance, PaperInstanceItem, Submission


PROFILE_QUESTION_TYPES = ("choice", "fill_blank", "true_false")
REFERENCE_QUESTION_TYPES = ("calculation", "solution")
EXPECTED_EVIDENCE_EXCLUSIONS = frozenset({
    "needs_review", "invalidated_paper", "unsupported_profile_question_type",
})
CONFIRMED_STATUSES = ("auto_confirmed", "teacher_confirmed")


def snapshot_knowledge_code(item: Any) -> str | None:
    snapshot = item.knowledge_point_snapshot if item is not None else None
    code = snapshot.get("id") if isinstance(snapshot, dict) else None
    return code if isinstance(code, str) and code.strip() else None


def attempt_problem(
    attempt: Any, item: Any, paper: Any, submission: Any, revision: Any,
    *, knowledge_codes: Collection[str] | None = None,
) -> str | None:
    if item is None or paper is None or submission is None:
        return "missing_ownership_record"
    if (
        attempt.student_id != submission.student_id
        or attempt.student_id != paper.student_id
        or submission.paper_instance_id != paper.id
        or attempt.question_id != item.question_id
    ):
        return "ownership_mismatch"
    if paper.status != "published":
        return "unpublished_paper"
    if paper.invalidated_at is not None:
        return "invalidated_paper"
    snapshot = item.question_snapshot
    if (
        not isinstance(snapshot, dict)
        or not isinstance(snapshot.get("question_text"), str)
        or not isinstance(snapshot.get("original_qid"), str)
        or snapshot.get("question_type") not in (*PROFILE_QUESTION_TYPES, *REFERENCE_QUESTION_TYPES)
        or type(snapshot.get("difficulty")) is not int
        or not 1 <= snapshot["difficulty"] <= 5
    ):
        return "invalid_question_snapshot"
    code = snapshot_knowledge_code(item)
    if code is None or (knowledge_codes is not None and code not in knowledge_codes):
        return "unmapped_knowledge_point"
    if revision is None:
        return "missing_current_revision"
    if attempt.answered_at.tzinfo is None or attempt.answered_at.utcoffset() is None:
        return "invalid_answered_at"
    if attempt.revision_no < 1:
        return "invalid_revision"
    if attempt.grade_status == "needs_review":
        return "needs_review"
    if attempt.grade_status not in CONFIRMED_STATUSES:
        return "invalid_grade_status"
    if type(attempt.is_correct) is not bool:
        return "confirmed_without_result"
    if snapshot["question_type"] not in PROFILE_QUESTION_TYPES:
        return "unsupported_profile_question_type"
    return None


def load_attempt_rows_many(
    db: Session, student_ids: Iterable[UUID], *, as_of: datetime | None = None,
) -> dict[UUID, list[tuple]]:
    ids = list(dict.fromkeys(student_ids))
    grouped: dict[UUID, list[tuple]] = {student_id: [] for student_id in ids}
    if not ids:
        return grouped
    query = (
        select(Attempt, PaperInstanceItem, PaperInstance, Submission, AttemptRevision)
        .outerjoin(PaperInstanceItem, PaperInstanceItem.id == Attempt.paper_instance_item_id)
        .outerjoin(PaperInstance, PaperInstance.id == PaperInstanceItem.paper_instance_id)
        .outerjoin(Submission, Submission.id == Attempt.submission_id)
        .outerjoin(AttemptRevision, (AttemptRevision.attempt_id == Attempt.id)
                   & (AttemptRevision.revision_no == Attempt.revision_no))
        .where(Attempt.student_id.in_(ids))
        .order_by(Attempt.student_id, Attempt.answered_at, Attempt.id)
    )
    if as_of is not None:
        query = query.where(Attempt.answered_at <= as_of)
    for row in db.execute(query):
        grouped[row[0].student_id].append(row)
    return grouped
