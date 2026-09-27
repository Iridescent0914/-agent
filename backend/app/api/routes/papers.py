from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.schemas.papers import (
    AlternativeOut, DraftMutationRequest, PaperByCodeOut, PaperInstanceStudentOut,
    PaperInstanceTeacherOut, PublishPaperRequest, ReorderItemsRequest, ReplaceItemRequest,
    WithdrawPaperRequest,
)
from app.services.paper_workflow import (
    PaperConflict, PaperValidation, alternatives, by_code, delete_draft, delete_item,
    publish_paper, reorder_items, replace_item, republish_paper, revise_paper,
    student_view, teacher_view, withdraw_paper,
)


router = APIRouter(prefix="/paper-instances", tags=["paper-instances"])


def _error(exc: Exception) -> HTTPException:
    if isinstance(exc, LookupError):
        return HTTPException(404, str(exc))
    if isinstance(exc, PaperConflict):
        return HTTPException(409, str(exc))
    return HTTPException(422, str(exc))


@router.get("/by-code/{instance_code}", response_model=PaperByCodeOut)
def find_by_code(instance_code: str, db: Session = Depends(get_db)):
    try:
        return by_code(db, instance_code)
    except (LookupError, ValueError) as exc:
        raise _error(exc) from exc


@router.get("/{paper_id}", response_model=PaperInstanceTeacherOut | PaperInstanceStudentOut)
def get_paper(paper_id: UUID, view: str = Query("teacher", pattern="^(teacher|student)$"),
              db: Session = Depends(get_db)):
    try:
        return student_view(db, paper_id) if view == "student" else teacher_view(db, paper_id)
    except (LookupError, ValueError) as exc:
        raise _error(exc) from exc


@router.get("/{paper_id}/items/{item_id}/alternatives", response_model=AlternativeOut)
def get_alternatives(paper_id: UUID, item_id: UUID, db: Session = Depends(get_db)):
    try:
        return alternatives(db, paper_id, item_id)
    except (LookupError, ValueError, RuntimeError) as exc:
        raise _error(exc) from exc


@router.post("/{paper_id}/items/{item_id}/replace", response_model=PaperInstanceTeacherOut)
def replace(paper_id: UUID, item_id: UUID, request: ReplaceItemRequest,
            db: Session = Depends(get_db)):
    try:
        return replace_item(
            db, paper_id, item_id, replacement_id=request.replacement_question_id,
            expected_revision=request.expected_edit_revision,
        )
    except (LookupError, ValueError, RuntimeError) as exc:
        raise _error(exc) from exc


@router.delete("/{paper_id}/items/{item_id}", response_model=PaperInstanceTeacherOut)
def remove(paper_id: UUID, item_id: UUID, request: DraftMutationRequest = Body(...),
           db: Session = Depends(get_db)):
    try:
        return delete_item(db, paper_id, item_id, expected_revision=request.expected_edit_revision)
    except (LookupError, ValueError, RuntimeError) as exc:
        raise _error(exc) from exc


@router.put("/{paper_id}/order", response_model=PaperInstanceTeacherOut)
def reorder(paper_id: UUID, request: ReorderItemsRequest, db: Session = Depends(get_db)):
    try:
        return reorder_items(
            db, paper_id, request.item_ids, expected_revision=request.expected_edit_revision
        )
    except (LookupError, ValueError, RuntimeError) as exc:
        raise _error(exc) from exc


@router.post("/{paper_id}/publish", response_model=PaperInstanceTeacherOut)
def publish(paper_id: UUID, request: PublishPaperRequest, db: Session = Depends(get_db)):
    try:
        publish_paper(
            db, paper_id, expected_revision=request.expected_edit_revision,
            acknowledge_shortfall=request.acknowledge_shortfall,
            acknowledgement_reason=request.acknowledgement_reason,
        )
        return teacher_view(db, paper_id)
    except (LookupError, ValueError, RuntimeError) as exc:
        raise _error(exc) from exc


@router.post("/{paper_id}/revise", response_model=PaperInstanceTeacherOut, status_code=201)
def revise(paper_id: UUID, db: Session = Depends(get_db)):
    try:
        draft = revise_paper(db, paper_id)
        return teacher_view(db, draft.id)
    except (LookupError, ValueError, RuntimeError) as exc:
        raise _error(exc) from exc


@router.delete('/{paper_id}')
def remove_draft(paper_id: UUID, request: DraftMutationRequest = Body(...),
                 db: Session = Depends(get_db)):
    try:
        deleted_id = delete_draft(
            db, paper_id, expected_revision=request.expected_edit_revision
        )
        return {'deleted_id': str(deleted_id)}
    except (LookupError, ValueError, RuntimeError) as exc:
        raise _error(exc) from exc


@router.post('/{paper_id}/withdraw', response_model=PaperInstanceTeacherOut)
def withdraw(paper_id: UUID, request: WithdrawPaperRequest,
             db: Session = Depends(get_db)):
    try:
        withdraw_paper(
            db, paper_id, expected_revision=request.expected_edit_revision,
            reason=request.reason,
        )
        return teacher_view(db, paper_id)
    except (LookupError, ValueError, RuntimeError) as exc:
        raise _error(exc) from exc


@router.post('/{paper_id}/republish', response_model=PaperInstanceTeacherOut)
def republish(paper_id: UUID, request: DraftMutationRequest,
              db: Session = Depends(get_db)):
    try:
        republish_paper(
            db, paper_id, expected_revision=request.expected_edit_revision,
        )
        return teacher_view(db, paper_id)
    except (LookupError, ValueError, RuntimeError) as exc:
        raise _error(exc) from exc
