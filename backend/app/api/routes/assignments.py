from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models import PaperInstance
from app.schemas.assignments import (
    AssignmentCreateRequest, AssignmentDetailOut, AssignmentListItem,
    AssignmentPlanRequest, AssignmentPreviewOut,
)
from app.schemas.common import Page
from app.schemas.papers import BatchPublishRequest, PaperSummaryOut
from app.services.paper_generation import create_assignment, preview_assignment
from app.services.paper_workflow import (
    PaperConflict, PaperValidation, assignment_detail, assignment_paper_summaries,
    delete_empty_assignment, list_assignment_items, list_recent_completed_assignments,
    publish_paper,
)


router = APIRouter(prefix="/assignments", tags=["assignments"])


def _error(exc: Exception) -> HTTPException:
    if isinstance(exc, LookupError):
        return HTTPException(404, str(exc))
    if isinstance(exc, PaperConflict) or isinstance(exc, RuntimeError):
        return HTTPException(409, str(exc))
    return HTTPException(422, str(exc))


@router.post("/preview", response_model=AssignmentPreviewOut)
def preview_many(request: AssignmentPlanRequest, db: Session = Depends(get_db)):
    try:
        return preview_assignment(db, request, as_of=datetime.now(timezone.utc))
    except (LookupError, ValueError) as exc:
        raise _error(exc) from exc


@router.post("", response_model=AssignmentDetailOut, status_code=201)
def create(request: AssignmentCreateRequest, db: Session = Depends(get_db)):
    try:
        assignment, _ = create_assignment(db, request, as_of=datetime.now(timezone.utc))
        return assignment_detail(db, assignment.id)
    except (LookupError, ValueError, RuntimeError) as exc:
        raise _error(exc) from exc


@router.get("", response_model=Page[AssignmentListItem])
def list_assignments(page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
                     class_name: str | None = Query(None, min_length=1, max_length=120),
                     chapter_code: str | None = Query(None, min_length=1, max_length=160),
                     db: Session = Depends(get_db)):
    items, total = list_assignment_items(
        db, offset=(page - 1) * page_size, limit=page_size,
        class_name=class_name, chapter_code=chapter_code,
    )
    return Page(items=items, total=total, page=page, page_size=page_size)


@router.get("/recent-completed", response_model=list[AssignmentListItem])
def recent_completed_assignments(
    limit: int = Query(5, ge=1, le=5),
    class_id: UUID | None = Query(None),
    db: Session = Depends(get_db),
):
    return list_recent_completed_assignments(db, limit=limit, class_id=class_id)


@router.get("/{assignment_id}", response_model=AssignmentDetailOut)
def get_assignment(
    assignment_id: UUID,
    include_papers: bool = Query(True),
    db: Session = Depends(get_db),
):
    try:
        return assignment_detail(db, assignment_id, include_papers=include_papers)
    except LookupError as exc:
        raise _error(exc) from exc


@router.get("/{assignment_id}/papers", response_model=Page[PaperSummaryOut])
def list_assignment_papers(
    assignment_id: UUID,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    try:
        items, total = assignment_paper_summaries(
            db, assignment_id, offset=(page - 1) * page_size, limit=page_size
        )
        return Page(items=items, total=total, page=page, page_size=page_size)
    except LookupError as exc:
        raise _error(exc) from exc


@router.delete("/{assignment_id}", status_code=204)
def delete_assignment(assignment_id: UUID, db: Session = Depends(get_db)) -> None:
    try:
        delete_empty_assignment(db, assignment_id)
    except (LookupError, ValueError, RuntimeError) as exc:
        raise _error(exc) from exc


@router.post("/{assignment_id}/publish", response_model=AssignmentDetailOut)
def publish_assignment(assignment_id: UUID, request: BatchPublishRequest,
                       db: Session = Depends(get_db)):
    try:
        requested_ids = [target.paper_instance_id for target in request.papers]
        allowed_count = int(db.scalar(select(func.count(PaperInstance.id)).where(
            PaperInstance.assignment_id == assignment_id,
            PaperInstance.id.in_(requested_ids),
        )) or 0)
        if allowed_count != len(requested_ids):
            raise PaperValidation("paper does not belong to this assignment")
        for target in request.papers:
            publish_paper(
                db, target.paper_instance_id,
                expected_revision=target.expected_edit_revision,
                acknowledge_shortfall=target.acknowledge_shortfall,
                acknowledgement_reason=target.acknowledgement_reason,
            )
        return assignment_detail(db, assignment_id, include_papers=False)
    except (LookupError, ValueError, RuntimeError) as exc:
        raise _error(exc) from exc

