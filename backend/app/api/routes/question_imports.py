from __future__ import annotations

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Header, Response, UploadFile
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import get_db
from app.models import QuestionImportBatch, QuestionImportItem
from app.services.question_import import (
    OWNER_SCOPE, ImportProblem, cancel_batch, create_batch, get_batch, retry_batch,
    update_candidate,
)
from app.services.question_import_s2_worker import start_s2
from app.services.question_import_s3 import reconcile_source_answers, restore_nonquestion_fragment
from app.services.question_import_s3_global import dedupe_batch
from app.services.question_import_s3_images import (
    crop_page, extract_images, image_bytes, list_images, render_page, set_candidate_image,
)
from app.services.question_import_s3_separate import start_separate_answers
from app.services.question_import_s3_review import (
    list_review_issues, list_review_units, review_issue, review_unit,
)


router = APIRouter(prefix="/question-imports", tags=["question-imports"])


class BatchOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    source_mode: str
    status: str
    stage: str
    files: list[dict]
    checkpoint: dict
    catalog_snapshot_sha256: str
    error_code: str | None
    error_message: str | None
    attempt_count: int
    expires_at: datetime
    files_purged_at: datetime | None
    created_at: datetime


class CandidateOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    batch_id: UUID
    ordinal: int
    revision: int
    payload: dict
    is_fake: bool


class CandidateUpdate(BaseModel):
    expected_revision: int = Field(ge=1)
    changes: dict


class S2StartRequest(BaseModel):
    budget_cny: float = Field(gt=0)


class S3ReviewRequest(BaseModel):
    budget_cny: float = Field(gt=0)


class ImageCropRequest(BaseModel):
    box: list[float] = Field(min_length=4, max_length=4)


class ImageLinkRequest(BaseModel):
    expected_revision: int = Field(ge=1)
    linked: bool


@router.post("", response_model=BatchOut, status_code=201)
async def upload_batch(
    response: Response,
    source_mode: str = Form(...),
    question_file: UploadFile = File(...),
    answer_file: UploadFile | None = File(None),
    db: Session = Depends(get_db),
):
    limit = get_settings().question_import_max_file_bytes
    primary = await question_file.read(limit + 1)
    if len(primary) > limit:
        raise ImportProblem("FILE_LIMIT_EXCEEDED", "题目文件超过单文件上限", 413)
    files = [("questions", question_file.filename or "", primary)]
    if answer_file is not None:
        secondary = await answer_file.read(limit + 1)
        if len(secondary) > limit:
            raise ImportProblem("FILE_LIMIT_EXCEEDED", "答案文件超过单文件上限", 413)
        files.append(("answers", answer_file.filename or "", secondary))
    batch = create_batch(db, source_mode=source_mode, files=files)
    if batch.reused_existing:
        response.status_code = 200
    return batch


@router.get("", response_model=list[BatchOut])
def list_batches(db: Session = Depends(get_db)):
    return list(db.scalars(select(QuestionImportBatch).where(
        QuestionImportBatch.owner_scope == OWNER_SCOPE
    ).order_by(QuestionImportBatch.created_at.desc(), QuestionImportBatch.id.desc()).limit(100)))


@router.get("/s2-config")
def s2_config():
    settings = get_settings()
    return {"ready": bool(settings.question_import_s2_enabled and settings.question_import_s2_api_key
                          and settings.question_import_s2_operator_token
                          and settings.question_import_s2_base_url.startswith("https://")),
            "model": settings.question_import_s2_model,
            "max_budget_cny": settings.question_import_s2_max_budget_cny,
            "max_output_tokens": settings.question_import_s2_max_output_tokens,
            "pages_per_unit": settings.question_import_s2_pages_per_unit,
            "overlap_pages": settings.question_import_s2_overlap_pages}


@router.get("/{batch_id}", response_model=BatchOut)
def read_batch(batch_id: UUID, db: Session = Depends(get_db)):
    return get_batch(db, batch_id)


@router.post("/{batch_id}/cancel", response_model=BatchOut)
def cancel(batch_id: UUID, db: Session = Depends(get_db)):
    return cancel_batch(db, batch_id)


@router.post("/{batch_id}/retry", response_model=BatchOut)
def retry(batch_id: UUID, db: Session = Depends(get_db),
          operator_token: str | None = Header(None, alias="X-Import-S2-Token")):
    return retry_batch(db, batch_id, operator_token=operator_token)


@router.post("/{batch_id}/start-s2", response_model=BatchOut)
def start_extraction(batch_id: UUID, request: S2StartRequest, db: Session = Depends(get_db),
                     operator_token: str | None = Header(None, alias="X-Import-S2-Token")):
    return start_s2(db, batch_id, budget_cny=request.budget_cny, operator_token=operator_token)


@router.get("/{batch_id}/items", response_model=list[CandidateOut])
def list_items(batch_id: UUID, db: Session = Depends(get_db)):
    get_batch(db, batch_id)
    return list(db.scalars(select(QuestionImportItem).where(
        QuestionImportItem.batch_id == batch_id
    ).order_by(QuestionImportItem.ordinal)))


@router.get("/{batch_id}/s3/images")
def read_images(batch_id: UUID, db: Session = Depends(get_db)):
    return list_images(db, batch_id)


@router.post("/{batch_id}/s3/images")
def collect_images(batch_id: UUID, db: Session = Depends(get_db)):
    return extract_images(db, batch_id)


@router.get("/{batch_id}/s3/pages/{page_number}/preview")
def read_question_page(batch_id: UUID, page_number: int, db: Session = Depends(get_db)):
    return Response(content=render_page(db, batch_id, page_number), media_type="image/png",
                    headers={"Cache-Control": "private, no-store"})


@router.post("/{batch_id}/s3/pages/{page_number}/crop")
def crop_question_page(batch_id: UUID, page_number: int, request: ImageCropRequest,
                       db: Session = Depends(get_db)):
    return crop_page(db, batch_id, page_number, request.box)


@router.get("/{batch_id}/s3/images/{asset_id}/content")
def read_question_image(batch_id: UUID, asset_id: UUID, db: Session = Depends(get_db)):
    return Response(content=image_bytes(db, batch_id, asset_id), media_type="image/png",
                    headers={"Cache-Control": "private, no-store"})


@router.post("/{batch_id}/s3/images/{asset_id}/items/{item_id}", response_model=CandidateOut)
def link_question_image(batch_id: UUID, asset_id: UUID, item_id: UUID, request: ImageLinkRequest,
                        db: Session = Depends(get_db)):
    return set_candidate_image(db, batch_id, item_id, asset_id,
                               expected_revision=request.expected_revision, linked=request.linked)


@router.post("/{batch_id}/s3/reconcile-source-answers")
def reconcile_extracted_source_answers(batch_id: UUID, db: Session = Depends(get_db)):
    """Link only unambiguous source answers; never confirm or import them."""
    return reconcile_source_answers(db, batch_id, force=True)


@router.post("/{batch_id}/s3/start-separate-answers", response_model=BatchOut)
def start_separate_answer_extraction(batch_id: UUID, request: S3ReviewRequest,
                                     db: Session = Depends(get_db),
                                     operator_token: str | None = Header(None, alias="X-Import-S2-Token")):
    return start_separate_answers(db, batch_id, budget_cny=request.budget_cny,
                                  operator_token=operator_token)


@router.post("/{batch_id}/s3/dedupe-batch")
def run_batch_dedup(batch_id: UUID, request: S3ReviewRequest, db: Session = Depends(get_db),
                    operator_token: str | None = Header(None, alias="X-Import-S2-Token")):
    return dedupe_batch(db, batch_id, budget_cny=request.budget_cny, operator_token=operator_token)


@router.post("/{batch_id}/s3/nonquestions/{unit_index}/{fragment_index}/restore")
def restore_nonquestion(batch_id: UUID, unit_index: int, fragment_index: int,
                        db: Session = Depends(get_db)):
    return restore_nonquestion_fragment(db, batch_id, unit_index, fragment_index)


@router.get("/{batch_id}/s3/review-issues")
def read_review_issues(batch_id: UUID, db: Session = Depends(get_db)):
    return list_review_issues(db, batch_id)


@router.get("/{batch_id}/s3/review-units")
def read_review_units(batch_id: UUID, db: Session = Depends(get_db)):
    return list_review_units(db, batch_id)


@router.post("/{batch_id}/s3/review-units/{unit_index}")
def run_unit_review(batch_id: UUID, unit_index: int, request: S3ReviewRequest,
                    db: Session = Depends(get_db),
                    operator_token: str | None = Header(None, alias="X-Import-S2-Token")):
    return review_unit(db, batch_id, unit_index, budget_cny=request.budget_cny,
                       operator_token=operator_token)


@router.post("/{batch_id}/s3/review-issues/{key}")
def run_issue_review(batch_id: UUID, key: str, request: S3ReviewRequest,
                     db: Session = Depends(get_db),
                     operator_token: str | None = Header(None, alias="X-Import-S2-Token")):
    return review_issue(db, batch_id, key, budget_cny=request.budget_cny,
                        operator_token=operator_token)


@router.patch("/{batch_id}/items/{item_id}", response_model=CandidateOut)
def patch_item(batch_id: UUID, item_id: UUID, request: CandidateUpdate,
               db: Session = Depends(get_db)):
    return update_candidate(db, batch_id, item_id,
                            expected_revision=request.expected_revision,
                            changes=request.changes)
