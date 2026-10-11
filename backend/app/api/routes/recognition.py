from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import get_db
from app.schemas.recognition import PrintViewOut, RecognitionReviewRequest, RecognitionRunOut
from app.schemas.submissions import SubmissionDetailOut
from app.services.print_view import build_print_view
from app.services.recognition_workflow import (
    commit_run, create_run, discard_run, extract_run, get_current_run, get_run, image_path,
    review_recognition,
)
from app.services.submission_workflow import GradingConflict, GradingValidation


router = APIRouter(tags=["recognition"])


def _error(exc: Exception) -> HTTPException:
    if isinstance(exc, LookupError):
        return HTTPException(404, str(exc))
    if isinstance(exc, GradingConflict):
        return HTTPException(409, str(exc))
    return HTTPException(422, str(exc))


@router.get("/paper-instances/{paper_id}/print-view", response_model=PrintViewOut)
def print_view(paper_id: UUID, db: Session = Depends(get_db)):
    try:
        return build_print_view(db, paper_id)
    except (LookupError, ValueError) as exc:
        raise _error(exc) from exc


@router.post(
    "/paper-instances/{paper_id}/recognition-runs",
    response_model=RecognitionRunOut,
    status_code=201,
)
async def upload_recognition(
    paper_id: UUID,
    file: UploadFile = File(...),
    idempotency_key: str = Form(...),
    instance_code: str = Form(...),
    db: Session = Depends(get_db),
):
    limit = get_settings().recognition_max_upload_bytes
    data = await file.read(limit + 1)
    if len(data) > limit:
        raise HTTPException(413, "图片超过大小限制")
    try:
        return create_run(
            db,
            paper_id,
            instance_code=instance_code,
            idempotency_key=idempotency_key,
            filename=file.filename,
            data=data,
        )
    except (LookupError, ValueError, RuntimeError) as exc:
        raise _error(exc) from exc


@router.get(
    "/paper-instances/{paper_id}/recognition-runs/current",
    response_model=RecognitionRunOut | None,
)
def read_current_run(paper_id: UUID, db: Session = Depends(get_db)):
    try:
        return get_current_run(db, paper_id)
    except LookupError as exc:
        raise _error(exc) from exc


@router.post("/recognition-runs/{run_id}/extract", response_model=RecognitionRunOut)
def extract(run_id: UUID, db: Session = Depends(get_db)):
    try:
        return extract_run(db, run_id, release_lock_before_provider=True)
    except (LookupError, ValueError, RuntimeError) as exc:
        raise _error(exc) from exc


@router.get("/recognition-runs/{run_id}", response_model=RecognitionRunOut)
def read_run(run_id: UUID, db: Session = Depends(get_db)):
    try:
        return get_run(db, run_id)
    except LookupError as exc:
        raise _error(exc) from exc


@router.get("/recognition-runs/{run_id}/image")
def read_image(run_id: UUID, db: Session = Depends(get_db)):
    try:
        path, media_type = image_path(db, run_id)
        return FileResponse(path, media_type=media_type, filename="answer-region.jpg")
    except LookupError as exc:
        raise _error(exc) from exc


@router.post(
    "/recognition-runs/{run_id}/commit",
    response_model=SubmissionDetailOut,
    status_code=201,
)
def commit(run_id: UUID, db: Session = Depends(get_db)):
    try:
        return commit_run(db, run_id)
    except (LookupError, ValueError, RuntimeError) as exc:
        raise _error(exc) from exc


@router.post(
    "/submissions/{submission_id}/recognition-review",
    response_model=SubmissionDetailOut,
)
def review(submission_id: UUID, request: RecognitionReviewRequest,
           db: Session = Depends(get_db)):
    try:
        return review_recognition(db, submission_id, request)
    except (LookupError, ValueError, RuntimeError) as exc:
        raise _error(exc) from exc


@router.delete("/recognition-runs/{run_id}", status_code=204)
def discard(run_id: UUID, db: Session = Depends(get_db)):
    try:
        discard_run(db, run_id)
        return Response(status_code=204)
    except (LookupError, ValueError, RuntimeError) as exc:
        raise _error(exc) from exc
