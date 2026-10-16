from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
from typing import Any
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import (
    Assignment, Attempt, AttemptRevision, PaperInstance, PaperInstanceItem,
    Question, RecognitionRun, Student, Submission,
)
from app.schemas.submissions import (
    AttemptOut, AttemptReviewRequest, AttemptRevisionOut, GradingQueueItem,
    GradingQueueOut, ManualAnswerIn, ManualSubmissionRequest, SubmissionCounts,
    SubmissionDetailOut, SubmissionPaperItemOut, SubmissionRegradeRequest,
)
from app.services.assets import resolve_assets
from app.services.grading import grade_answer
from app.services.paper_snapshots import current_exam_point_snapshot
from app.services.student_profile import load_context, persist_mastery


class GradingConflict(RuntimeError):
    pass


class GradingValidation(ValueError):
    pass


def _attempt_state(attempt: Attempt) -> dict[str, Any]:
    return {
        "student_answer": attempt.student_answer,
        "normalized_answer": attempt.normalized_answer,
        "grading_details": attempt.grading_details or {},
        "grade_status": attempt.grade_status,
        "is_correct": attempt.is_correct,
        "recognition_status": attempt.recognition_status,
        "ocr_confidence": float(attempt.ocr_confidence) if isinstance(attempt.ocr_confidence, Decimal) else None,
        "manual_override": attempt.manual_override,
        "revision_no": attempt.revision_no,
        "answered_at": attempt.answered_at.isoformat(),
        "reviewed_at": attempt.reviewed_at.isoformat() if attempt.reviewed_at else None,
    }


def _state_core(attempt: Attempt) -> tuple[Any, ...]:
    return (
        attempt.student_answer, attempt.normalized_answer, attempt.grading_details or {},
        attempt.grade_status, attempt.is_correct, attempt.manual_override,
    )


def _fingerprint(paper_id: UUID, student_id: UUID, answers: list[ManualAnswerIn]) -> str:
    payload = {
        "paper_instance_id": str(paper_id),
        "student_id": str(student_id),
        "answers": [
            {
                "paper_instance_item_id": str(item.paper_instance_item_id),
                "order_no": item.order_no,
                "response_status": item.response_status,
                "answer": item.answer,
            }
            for item in sorted(answers, key=lambda value: str(value.paper_instance_item_id))
        ],
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _items(db: Session, paper_id: UUID) -> list[PaperInstanceItem]:
    return list(db.scalars(
        select(PaperInstanceItem)
        .where(PaperInstanceItem.paper_instance_id == paper_id)
        .order_by(PaperInstanceItem.order_no, PaperInstanceItem.id)
    ))


def _question_answer_rule(question: Question) -> dict[str, Any]:
    return {
        "answer_match_type": question.answer_match_type,
        "answer_normalization_rule": deepcopy(question.answer_normalization_rule or {}),
    }


def _grading_reference(
    item: PaperInstanceItem, attempt: Attempt | None,
) -> tuple[Any, dict[str, Any], str]:
    details = (attempt.grading_details or {}) if attempt is not None else {}
    if details.get("reference_source") == "question_revision":
        return (
            deepcopy(details.get("reference_answer_snapshot")),
            deepcopy(details.get("reference_answer_rule_snapshot") or {}),
            "question_revision",
        )
    return deepcopy(item.answer_snapshot), deepcopy(item.answer_rule_snapshot), "published_snapshot"


def _initial_grading_reference(
    item: PaperInstanceItem, question: Question | None,
) -> tuple[Any, dict[str, Any], str]:
    """Resolve the answer key that exists when an unsubmitted paper is entered."""
    if (
        question is not None
        and question.archived_at is None
        and question.grading_readiness == "machine_ready"
    ):
        answer_rule = _question_answer_rule(question)
        if (
            question.correct_answer != item.answer_snapshot
            or answer_rule != item.answer_rule_snapshot
        ):
            return deepcopy(question.correct_answer), answer_rule, "question_revision"
    return deepcopy(item.answer_snapshot), deepcopy(item.answer_rule_snapshot), "published_snapshot"


def _reference_details(
    result_details: dict[str, Any], question: Question, answer_rule: dict[str, Any],
    applied_at: datetime, *, regraded: bool,
) -> dict[str, Any]:
    details = {
        **result_details,
        "reference_source": "question_revision",
        "reference_answer_snapshot": deepcopy(question.correct_answer),
        "reference_answer_rule_snapshot": deepcopy(answer_rule),
        "reference_question_revision": question.review_revision,
        "reference_applied_at": applied_at.isoformat(),
    }
    if regraded:
        details["regraded_at"] = applied_at.isoformat()
    return details


def _validate_answer(
    item: PaperInstanceItem, incoming: ManualAnswerIn,
    reference: tuple[Any, dict[str, Any]] | None = None,
) -> None:
    if incoming.order_no != item.order_no:
        raise GradingValidation("answer item order does not match published paper")
    if incoming.response_status == "blank":
        return
    expected, answer_rule = reference or (item.answer_snapshot, item.answer_rule_snapshot)
    match_type = (answer_rule or {}).get("answer_match_type")
    if match_type != "group":
        if not isinstance(incoming.answer, str):
            raise GradingValidation("single answer must be text")
        return
    actual = incoming.answer
    if (not isinstance(expected, dict) or expected.get("kind") != "group"
            or not isinstance(expected.get("items"), list)):
        raise GradingValidation("published group answer snapshot is invalid")
    if (not isinstance(actual, dict) or actual.get("kind") != "group"
            or not isinstance(actual.get("items"), list)):
        raise GradingValidation("group answer has invalid shape")
    expected_ids = [part.get("item_id") for part in expected["items"] if isinstance(part, dict)]
    actual_ids = [part.get("item_id") for part in actual["items"] if isinstance(part, dict)]
    if (len(expected_ids) != len(expected["items"]) or len(actual_ids) != len(actual["items"])
            or len(actual_ids) != len(set(actual_ids)) or set(expected_ids) != set(actual_ids)):
        raise GradingValidation("group answer item ids do not match published snapshot")
    if any(part.get("answer") is not None and not isinstance(part.get("answer"), str)
           for part in actual["items"]):
        raise GradingValidation("group item answer must be text or null")


def _attempt_out(attempt: Attempt, order_no: int) -> AttemptOut:
    return AttemptOut(
        id=attempt.id, paper_instance_item_id=attempt.paper_instance_item_id,
        order_no=order_no, student_answer=attempt.student_answer,
        normalized_answer=attempt.normalized_answer,
        grading_details=attempt.grading_details or {}, grade_status=attempt.grade_status,
        is_correct=attempt.is_correct, manual_override=attempt.manual_override,
        revision_no=attempt.revision_no, answered_at=attempt.answered_at,
        reviewed_at=attempt.reviewed_at,
        used_in_profile=attempt.grade_status in {"auto_confirmed", "teacher_confirmed"},
        recognition_status=attempt.recognition_status,
        ocr_confidence=(
            float(attempt.ocr_confidence) if attempt.ocr_confidence is not None else None
        ),
    )


def submission_view(db: Session, paper_id: UUID, *, replayed: bool = False,
                    unchanged: bool = False,
                    mastery_stats: dict[str, int] | None = None,
                    regraded_count: int = 0) -> SubmissionDetailOut:
    paper = db.get(PaperInstance, paper_id)
    if paper is None:
        raise LookupError("paper instance not found")
    student = db.get(Student, paper.student_id)
    assignment = db.get(Assignment, paper.assignment_id)
    if student is None or assignment is None:
        raise GradingValidation("paper ownership is incomplete")
    submission = db.scalar(select(Submission).where(Submission.paper_instance_id == paper.id))
    attempts = {}
    recognition_run = None
    if submission is not None:
        recognition_run = db.scalar(
            select(RecognitionRun).where(RecognitionRun.submission_id == submission.id)
        )
        attempts = {
            value.paper_instance_item_id: value
            for value in db.scalars(
                select(Attempt).where(Attempt.submission_id == submission.id)
                .order_by(Attempt.answered_at, Attempt.id)
            )
        }
    output_items = []
    answer_key_update_count = 0
    for item in _items(db, paper.id):
        attempt = attempts.get(item.id)
        question = db.get(Question, item.question_id)
        if attempt is None:
            grading_answer, grading_rule, answer_source = _initial_grading_reference(
                item, question,
            )
        else:
            grading_answer, grading_rule, answer_source = _grading_reference(item, attempt)
        answer_key_update_available = bool(
            attempt is not None
            and question is not None
            and question.archived_at is None
            and question.grading_readiness == "machine_ready"
            and (
                question.correct_answer != grading_answer
                or _question_answer_rule(question) != grading_rule
            )
        )
        answer_key_update_count += int(answer_key_update_available)
        output_items.append(SubmissionPaperItemOut(
            id=item.id, question_id=item.question_id, order_no=item.order_no,
            question_snapshot=item.question_snapshot, options_snapshot=item.options_snapshot,
            answer_snapshot=item.answer_snapshot, answer_rule_snapshot=item.answer_rule_snapshot,
            grading_answer_snapshot=grading_answer,
            grading_answer_rule_snapshot=grading_rule,
            standard_answer_source=answer_source,
            answer_key_update_available=answer_key_update_available,
            exam_point_snapshot=current_exam_point_snapshot(
                db, item.question_id, item.exam_point_snapshot
            ),
            assets=resolve_assets(item.question_snapshot.get("assets", [])),
            attempt=_attempt_out(attempt, item.order_no) if attempt else None,
        ))
    values = list(attempts.values())
    return SubmissionDetailOut(
        submission_id=submission.id if submission else None,
        paper_instance_id=paper.id, assignment_id=assignment.id,
        assignment_title=assignment.title, instance_code=paper.instance_code,
        version_no=paper.version_no, paper_status=paper.status,
        is_active=paper.status == "published" and paper.invalidated_at is None,
        can_submit=(submission is None and paper.status == "published" and paper.invalidated_at is None),
        student_id=student.id, student_code=student.student_code,
        student_name=student.display_name, source=submission.source if submission else None,
        recognition_run_id=recognition_run.id if recognition_run else None,
        recognition_image_url=(
            f"/recognition-runs/{recognition_run.id}/image"
            if recognition_run and recognition_run.image_purged_at is None
            and (
                get_settings().recognition_upload_dir
                / recognition_run.image_storage_key
            ).is_file()
            else None
        ),
        recognition_image_purged_at=(
            recognition_run.image_purged_at if recognition_run else None
        ),
        recognition_provider=recognition_run.provider if recognition_run else None,
        recognition_model=recognition_run.model if recognition_run else None,
        recognition_prompt_version=recognition_run.prompt_version if recognition_run else None,
        submitted_at=submission.submitted_at if submission else None,
        replayed=replayed, unchanged=unchanged,
        answer_key_update_count=answer_key_update_count,
        regraded_count=regraded_count,
        summary=SubmissionCounts(
            total_count=len(values),
            correct_count=sum(value.is_correct is True for value in values),
            incorrect_count=sum(value.is_correct is False for value in values),
            needs_review_count=sum(value.grade_status == "needs_review" for value in values),
        ), items=output_items, mastery_update_stats=mastery_stats,
    )


def submission_by_id(db: Session, submission_id: UUID) -> SubmissionDetailOut:
    submission = db.get(Submission, submission_id)
    if submission is None:
        raise LookupError("submission not found")
    return submission_view(db, submission.paper_instance_id)


def create_manual_submission(db: Session, paper_id: UUID,
                             request: ManualSubmissionRequest) -> SubmissionDetailOut:
    initial_paper = db.get(PaperInstance, paper_id)
    if initial_paper is None:
        raise LookupError("paper instance not found")
    student = db.scalar(
        select(Student).where(Student.id == initial_paper.student_id).with_for_update()
    )
    if student is None or student.archived_at is not None:
        raise GradingValidation("student is unavailable")
    paper = db.scalar(select(PaperInstance).where(PaperInstance.id == paper_id).with_for_update())
    if paper is None:
        raise LookupError("paper instance not found")
    if request.student_id != paper.student_id:
        raise GradingValidation("student does not match paper instance")
    if paper.status != "published" or paper.invalidated_at is not None:
        raise GradingValidation("paper is not an active published version")

    paper_items = _items(db, paper.id)
    question_ids = sorted({item.question_id for item in paper_items}, key=str)
    questions = {
        question.id: question
        for question in db.scalars(
            select(Question).where(Question.id.in_(question_ids))
            .order_by(Question.id).with_for_update()
        )
    }
    grading_references = {
        item.id: _initial_grading_reference(item, questions.get(item.question_id))
        for item in paper_items
    }
    incoming = {item.paper_instance_item_id: item for item in request.answers}
    if set(incoming) != {item.id for item in paper_items}:
        raise GradingValidation("answer set must exactly match published paper items")
    for item in paper_items:
        reference_answer, reference_rule, _ = grading_references[item.id]
        _validate_answer(item, incoming[item.id], (reference_answer, reference_rule))
    fingerprint = _fingerprint(paper.id, paper.student_id, request.answers)

    key_owner = db.scalar(
        select(Submission).where(Submission.idempotency_key == request.idempotency_key)
        .with_for_update()
    )
    if key_owner is not None and key_owner.paper_instance_id != paper.id:
        raise GradingConflict("idempotency key belongs to another paper")
    existing = db.scalar(
        select(Submission).where(Submission.paper_instance_id == paper.id).with_for_update()
    )
    if existing is not None:
        if existing.request_fingerprint == fingerprint:
            return submission_view(db, paper.id, replayed=True)
        if existing.idempotency_key == request.idempotency_key:
            raise GradingConflict("idempotency key was reused with a different payload")
        raise GradingConflict("paper already has a different submission; use teacher review")

    answered_at = request.answered_at or datetime.now(timezone.utc)
    submission = Submission(
        student_id=paper.student_id, paper_instance_id=paper.id, source="manual",
        idempotency_key=request.idempotency_key, request_fingerprint=fingerprint,
        attachments=[], submitted_at=answered_at,
    )
    db.add(submission)
    db.flush()
    attempts = []
    for item in paper_items:
        answer = incoming[item.id]
        reference_answer, reference_rule, reference_source = grading_references[item.id]
        result = grade_answer(
            answer.answer, reference_answer, reference_rule,
            response_status=answer.response_status,
        )
        grading_details = result.details()
        if reference_source == "question_revision":
            grading_details = _reference_details(
                grading_details, questions[item.question_id], reference_rule,
                answered_at, regraded=False,
            )
        attempt = Attempt(
            submission_id=submission.id, student_id=paper.student_id,
            paper_instance_item_id=item.id, question_id=item.question_id,
            student_answer=answer.answer, normalized_answer=result.normalized_answer,
            grading_details=grading_details, grade_status=result.grade_status,
            is_correct=result.is_correct, ocr_confidence=None, recognition_status=None,
            manual_override=False, revision_no=1, answered_at=answered_at,
            reviewed_at=None,
        )
        db.add(attempt)
        attempts.append(attempt)
    db.flush()
    for attempt in attempts:
        db.add(AttemptRevision(
            attempt_id=attempt.id, revision_no=1, before_state=None,
            after_state=_attempt_state(attempt), operator_type="system",
            operator_id="grading_engine", reason="manual_submission_auto_grade",
            created_at=answered_at,
        ))
    db.flush()
    mastery_stats = persist_mastery(db, load_context(db, paper.student_id))
    return submission_view(db, paper.id, mastery_stats=mastery_stats)


def review_attempt(db: Session, attempt_id: UUID,
                   request: AttemptReviewRequest) -> SubmissionDetailOut:
    initial = db.get(Attempt, attempt_id)
    if initial is None:
        raise LookupError("attempt not found")
    student = db.scalar(select(Student).where(Student.id == initial.student_id).with_for_update())
    if student is None or student.archived_at is not None:
        raise GradingValidation("student is unavailable")
    attempt = db.scalar(select(Attempt).where(Attempt.id == attempt_id).with_for_update())
    if attempt is None:
        raise LookupError("attempt not found")
    if attempt.revision_no != request.expected_revision_no:
        raise GradingConflict("attempt was changed; refresh before reviewing")
    item = db.get(PaperInstanceItem, attempt.paper_instance_item_id)
    submission = db.get(Submission, attempt.submission_id)
    if item is None or submission is None:
        raise GradingValidation("attempt ownership is incomplete")

    before = _attempt_state(attempt)
    if request.action == "correct":
        reference_answer, reference_rule, reference_source = _grading_reference(item, attempt)
        incoming = ManualAnswerIn(
            paper_instance_item_id=item.id, order_no=item.order_no,
            response_status=request.corrected_response_status,
            answer=request.corrected_answer,
        )
        _validate_answer(item, incoming, (reference_answer, reference_rule))
        result = grade_answer(
            incoming.answer, reference_answer, reference_rule,
            response_status=incoming.response_status,
        )
        attempt.student_answer = incoming.answer
        attempt.normalized_answer = result.normalized_answer
        details = result.details()
        if reference_source == "question_revision":
            previous = attempt.grading_details or {}
            details.update({
                key: deepcopy(previous[key])
                for key in (
                    "reference_source", "reference_answer_snapshot",
                    "reference_answer_rule_snapshot", "reference_question_revision",
                    "reference_applied_at", "regraded_at",
                )
                if key in previous
            })
        attempt.grading_details = details

    attempt.grade_status = "needs_review" if request.action == "reopen" else "teacher_confirmed"
    attempt.is_correct = None if request.action == "reopen" else request.is_correct
    attempt.manual_override = True
    target_core = _state_core(attempt)

    current_core = (
        before["student_answer"], before["normalized_answer"], before["grading_details"],
        before["grade_status"], before["is_correct"], before["manual_override"],
    )
    if target_core == current_core:
        db.refresh(attempt)
        return submission_view(db, submission.paper_instance_id, unchanged=True)

    attempt.revision_no += 1
    attempt.reviewed_at = datetime.now(timezone.utc)
    db.add(AttemptRevision(
        attempt_id=attempt.id, revision_no=attempt.revision_no,
        before_state=before, after_state=_attempt_state(attempt),
        operator_type="teacher", operator_id=request.actor, reason=request.reason,
    ))
    db.flush()
    mastery_stats = persist_mastery(db, load_context(db, attempt.student_id))
    return submission_view(db, submission.paper_instance_id, mastery_stats=mastery_stats)


def regrade_submission(
    db: Session, submission_id: UUID, request: SubmissionRegradeRequest,
) -> SubmissionDetailOut:
    initial = db.get(Submission, submission_id)
    if initial is None:
        raise LookupError("submission not found")
    student = db.scalar(
        select(Student).where(Student.id == initial.student_id).with_for_update()
    )
    if student is None or student.archived_at is not None:
        raise GradingValidation("student is unavailable")
    submission = db.scalar(
        select(Submission).where(Submission.id == submission_id).with_for_update()
    )
    if submission is None:
        raise LookupError("submission not found")
    attempts = list(db.scalars(
        select(Attempt).where(Attempt.submission_id == submission.id)
        .order_by(Attempt.id).with_for_update()
    ))
    items = {
        item.id: item
        for item in _items(db, submission.paper_instance_id)
    }
    question_ids = sorted({attempt.question_id for attempt in attempts}, key=str)
    questions = {
        question.id: question
        for question in db.scalars(
            select(Question).where(Question.id.in_(question_ids))
            .order_by(Question.id).with_for_update()
        )
    }
    now = datetime.now(timezone.utc)
    changed = 0
    for attempt in attempts:
        item = items.get(attempt.paper_instance_item_id)
        question = questions.get(attempt.question_id)
        if item is None or question is None:
            raise GradingValidation("attempt grading ownership is incomplete")
        if question.archived_at is not None or question.grading_readiness != "machine_ready":
            continue
        answer_rule = _question_answer_rule(question)
        effective_answer, effective_rule, _ = _grading_reference(item, attempt)
        if question.correct_answer == effective_answer and answer_rule == effective_rule:
            continue

        before = _attempt_state(attempt)
        response_status = "blank" if attempt.student_answer is None else "answered"
        result = grade_answer(
            attempt.student_answer,
            question.correct_answer,
            answer_rule,
            response_status=response_status,
        )
        attempt.normalized_answer = result.normalized_answer
        attempt.grading_details = _reference_details(
            result.details(), question, answer_rule, now, regraded=True,
        )
        attempt.grade_status = result.grade_status
        attempt.is_correct = result.is_correct
        attempt.manual_override = False
        attempt.revision_no += 1
        attempt.reviewed_at = now
        db.add(AttemptRevision(
            attempt_id=attempt.id,
            revision_no=attempt.revision_no,
            before_state=before,
            after_state=_attempt_state(attempt),
            operator_type="teacher",
            operator_id=request.actor,
            reason=request.reason,
            created_at=now,
        ))
        changed += 1

    if changed == 0:
        return submission_view(db, submission.paper_instance_id, unchanged=True)
    db.flush()
    mastery_stats = persist_mastery(db, load_context(db, submission.student_id))
    return submission_view(
        db,
        submission.paper_instance_id,
        mastery_stats=mastery_stats,
        regraded_count=changed,
    )


def revision_history(db: Session, attempt_id: UUID) -> list[AttemptRevisionOut]:
    if db.get(Attempt, attempt_id) is None:
        raise LookupError("attempt not found")
    return [AttemptRevisionOut(
        revision_no=row.revision_no, before_state=row.before_state,
        after_state=row.after_state, operator_type=row.operator_type,
        operator_id=row.operator_id, reason=row.reason, created_at=row.created_at,
    ) for row in db.scalars(
        select(AttemptRevision).where(AttemptRevision.attempt_id == attempt_id)
        .order_by(AttemptRevision.revision_no)
    )]


def grading_queue(db: Session, *, offset: int = 0, limit: int = 100,
                  status: str | None = None) -> GradingQueueOut:
    item_counts = (
        select(
            PaperInstanceItem.paper_instance_id.label("paper_id"),
            func.count(PaperInstanceItem.id).label("item_count"),
        )
        .group_by(PaperInstanceItem.paper_instance_id)
        .subquery()
    )
    pending_counts = (
        select(
            Attempt.submission_id.label("submission_id"),
            func.count(Attempt.id).label("pending_count"),
        )
        .where(Attempt.grade_status == "needs_review")
        .group_by(Attempt.submission_id)
        .subquery()
    )
    pending = func.coalesce(pending_counts.c.pending_count, 0)
    query = (
        select(
            PaperInstance, Assignment, Student, Submission,
            func.coalesce(item_counts.c.item_count, 0), pending,
        )
        .join(Assignment, Assignment.id == PaperInstance.assignment_id)
        .join(Student, Student.id == PaperInstance.student_id)
        .outerjoin(Submission, Submission.paper_instance_id == PaperInstance.id)
        .outerjoin(item_counts, item_counts.c.paper_id == PaperInstance.id)
        .outerjoin(
            pending_counts,
            pending_counts.c.submission_id == Submission.id,
        )
        .where(
            PaperInstance.status == "published",
            or_(PaperInstance.invalidated_at.is_(None), Submission.id.is_not(None)),
        )
    )
    if status == "unsubmitted":
        query = query.where(Submission.id.is_(None))
    elif status == "needs_review":
        query = query.where(Submission.id.is_not(None), pending > 0)
    elif status == "completed":
        query = query.where(Submission.id.is_not(None), pending == 0)
    total = int(db.scalar(select(func.count()).select_from(
        query.order_by(None).subquery()
    )) or 0)
    rows = db.execute(
        query.order_by(PaperInstance.published_at.desc(), PaperInstance.id)
        .offset(offset).limit(limit)
    ).all()
    output = []
    for paper, assignment, student, submission, item_count, pending_count in rows:
        grade_status = (
            "unsubmitted" if submission is None
            else "needs_review" if pending_count else "completed"
        )
        output.append(GradingQueueItem(
            paper_instance_id=paper.id, assignment_id=assignment.id,
            assignment_title=assignment.title, student_id=student.id,
            student_code=student.student_code, student_name=student.display_name,
            version_no=paper.version_no, instance_code=paper.instance_code or "",
            is_active=paper.invalidated_at is None,
            invalidation_type=paper.invalidation_type, grading_status=grade_status,
            item_count=int(item_count), needs_review_count=int(pending_count),
            submitted_at=submission.submitted_at if submission else None,
        ))
    return GradingQueueOut(items=output, total=total)
