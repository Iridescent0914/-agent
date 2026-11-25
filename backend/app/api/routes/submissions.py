from __future__ import annotations

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.schemas.paper_accuracy import PaperAccuracyComparisonOut
from app.services.paper_accuracy import paper_accuracy_comparison
from app.schemas.submissions import (
    AttemptReviewRequest, AttemptRevisionOut, GradingQueueOut,
    ManualSubmissionRequest, SubmissionDetailOut, SubmissionRegradeRequest,
)
from app.services.submission_workflow import (
    GradingConflict, GradingValidation, create_manual_submission, grading_queue,
    regrade_submission, review_attempt, revision_history, submission_by_id,
    submission_view,
)


router = APIRouter(tags=["grading"])


def _error(exc: Exception) -> HTTPException:
    if isinstance(exc, LookupError):
        return HTTPException(404, str(exc))
    if isinstance(exc, GradingConflict):
        return HTTPException(409, str(exc))
    return HTTPException(422, str(exc))


@router.get("/grading/queue", response_model=GradingQueueOut)
def get_grading_queue(
    offset: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=200),
    status: Literal["unsubmitted", "needs_review", "completed"] | None = None,
    class_id: UUID | None = Query(None),
    db: Session = Depends(get_db),
):
    return grading_queue(
        db, offset=offset, limit=limit, status=status, class_id=class_id,
    )


@router.get("/paper-instances/{paper_id}/submission", response_model=SubmissionDetailOut)
def get_submission(paper_id: UUID, db: Session = Depends(get_db)):
    try:
        return submission_view(db, paper_id)
    except (LookupError, ValueError) as exc:
        raise _error(exc) from exc


@router.get("/paper-instances/{paper_id}/accuracy-comparison", response_model=PaperAccuracyComparisonOut)
def get_accuracy_comparison(paper_id: UUID, db: Session = Depends(get_db)):
    try:
        return paper_accuracy_comparison(db, paper_id)
    except LookupError as exc:
        raise _error(exc) from exc


@router.post(
    "/paper-instances/{paper_id}/submissions/manual",
    response_model=SubmissionDetailOut, status_code=201,
)
def submit_manual(paper_id: UUID, request: ManualSubmissionRequest,
                  db: Session = Depends(get_db)):
    try:
        return create_manual_submission(db, paper_id, request)
    except (LookupError, ValueError, RuntimeError) as exc:
        raise _error(exc) from exc


@router.get("/submissions/{submission_id}", response_model=SubmissionDetailOut)
def get_submission_by_id(submission_id: UUID, db: Session = Depends(get_db)):
    try:
        return submission_by_id(db, submission_id)
    except (LookupError, ValueError) as exc:
        raise _error(exc) from exc


@router.post("/submissions/{submission_id}/regrade", response_model=SubmissionDetailOut)
def regrade(submission_id: UUID, request: SubmissionRegradeRequest,
            db: Session = Depends(get_db)):
    try:
        return regrade_submission(db, submission_id, request)
    except (LookupError, ValueError, RuntimeError) as exc:
        raise _error(exc) from exc


@router.patch("/attempts/{attempt_id}/review", response_model=SubmissionDetailOut)
def patch_attempt(attempt_id: UUID, request: AttemptReviewRequest,
                  db: Session = Depends(get_db)):
    try:
        return review_attempt(db, attempt_id, request)
    except (LookupError, ValueError, RuntimeError) as exc:
        raise _error(exc) from exc


@router.get("/attempts/{attempt_id}/revisions", response_model=list[AttemptRevisionOut])
def get_revisions(attempt_id: UUID, db: Session = Depends(get_db)):
    try:
        return revision_history(db, attempt_id)
    except LookupError as exc:
        raise _error(exc) from exc
