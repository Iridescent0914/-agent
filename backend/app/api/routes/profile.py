from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.schemas.profile import AttemptEvidenceOut, ExamPointOut, MasteryHistoryOut, MasteryOut, ProfileOut
from app.schemas.recommendations import RecommendationOut, RecommendationRequest
from app.services.recommendations import preview
from app.services.student_profile import (
    ProfileContext, attempt_detail, build_profile, exam_point_catalog,
    history_for, load_context, mastery_items,
)


router = APIRouter(tags=["profiles"])


def student_context(student_id: UUID, db: Session = Depends(get_db)) -> ProfileContext:
    try:
        return load_context(db, student_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/exam-points", response_model=list[ExamPointOut])
def list_exam_points(chapter_code: str | None = None, db: Session = Depends(get_db)):
    return exam_point_catalog(db, chapter_code)


@router.get("/students/{student_id}/mastery", response_model=list[MasteryOut])
def get_mastery(chapter_code: str | None = None, exam_point_code: str | None = None,
                context: ProfileContext = Depends(student_context)):
    items = mastery_items(context, chapter_code)
    # Keep old bookmarked/filter URLs usable after curriculum metadata changes.
    # New responses and all calculations still use the current KP tracking code.
    effective_code = (
        context.exam_code_aliases.get(exam_point_code, exam_point_code)
        if exam_point_code is not None
        else None
    )
    return [
        item
        for item in items
        if effective_code is None or item.exam_point_code == effective_code
    ]


@router.get("/students/{student_id}/profile", response_model=ProfileOut)
def get_profile(context: ProfileContext = Depends(student_context)):
    return build_profile(context)


@router.get("/students/{student_id}/mastery/{exam_point_code}/history", response_model=MasteryHistoryOut)
def get_history(exam_point_code: str, context: ProfileContext = Depends(student_context)):
    try:
        return history_for(context, exam_point_code)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/students/{student_id}/attempts/{attempt_id}", response_model=AttemptEvidenceOut)
def get_evidence(attempt_id: UUID, context: ProfileContext = Depends(student_context)):
    record = next((record for record in context.records if record.attempt.id == attempt_id), None)
    if record is None:
        raise HTTPException(404, "attempt not found")
    return attempt_detail(record)


@router.post("/students/{student_id}/recommendations/preview", response_model=RecommendationOut)
def preview_recommendations(params: RecommendationRequest,
                             context: ProfileContext = Depends(student_context), db: Session = Depends(get_db)):
    try:
        return preview(db, context, params, as_of=datetime.now(timezone.utc))
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
