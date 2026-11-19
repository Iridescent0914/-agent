"""Internal preparation/suggestion endpoints; reviewer is an audit label, not authentication."""
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Response, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import get_db
from app.models import AnswerCardArtifact, AssessmentTask
from app.schemas.answer_card import AnswerCardArtifactOut, CreateAnswerCardArtifact
from app.schemas.answer_pages import CreatePageCollection, SelectPageImage
from app.schemas.vision_pages import CreatePageVisionTasks, PageVisionAction
from app.schemas.assessment import CreateAssessmentTask, QualityFeedbackIn, TaskAction
from app.schemas.vision_preparation import VisionInventory
from app.services import assessment as service
from app.services.equivalence_bailian import configuration
from app.services.assessment_worker import budget_state
from app.services.vision_preparation import build_inventory
from app.services import answer_card_artifacts as artifacts
from app.services import answer_pages
from app.services import vision_pages


def internal_only():
    # Until trusted teacher identities/ownership are available, fail closed in production.
    if get_settings().app_env not in {"development", "test"}:
        raise service.AssessmentProblem("MODEL_DISABLED", "S1入口仅供内部开发使用", 403)


router = APIRouter(prefix="/internal/stage6", tags=["stage6-internal"], dependencies=[Depends(internal_only)])


@router.get("/paper-instances/{paper_id}/vision-preparation", response_model=VisionInventory)
def vision_preparation(paper_id: UUID, db: Session = Depends(get_db)):
    service.enabled()
    try:
        return build_inventory(db, paper_id)
    except LookupError as exc:
        raise service.AssessmentProblem("NOT_FOUND", "试卷不存在", 404) from exc
    except ValueError as exc:
        raise service.AssessmentProblem("VISION_PREPARATION_BLOCKED", str(exc), 422) from exc


@router.get("/status")
def status(db: Session = Depends(get_db)):
    settings = get_settings()
    config_error = None
    try:
        config = configuration()
    except ValueError:
        config, config_error = {}, "CONFIG_INCOMPLETE"
    return {"enabled": settings.stage6_enabled, "provider": settings.stage6_equivalence_provider,
            "model": config.get("model"), "config_error": config_error,
            "policy_version": service.POLICY_VERSION,
            "real_provider_available": config.get("provider") == "bailian" and config_error is None,
            "budget": budget_state(db) if settings.stage6_equivalence_provider == "bailian" else None}


@router.post("/paper-instances/{paper_id}/answer-card-artifacts", response_model=AnswerCardArtifactOut)
def create_answer_card(paper_id: UUID, request: CreateAnswerCardArtifact, db: Session = Depends(get_db)):
    artifact, reused = artifacts.create_artifact(db, paper_id, request.expected_version_no)
    return artifacts.artifact_view(artifact, reused=reused)


@router.get("/paper-instances/{paper_id}/answer-card-artifacts", response_model=list[AnswerCardArtifactOut])
def list_answer_cards(paper_id: UUID, db: Session = Depends(get_db)):
    service.enabled()
    rows = db.scalars(select(AnswerCardArtifact).where(AnswerCardArtifact.paper_instance_id == paper_id)
                      .order_by(AnswerCardArtifact.revision_no.desc()).limit(50)).all()
    return [artifacts.artifact_view(row) for row in rows]


@router.get("/paper-instances/{paper_id}/answer-card-artifacts/{artifact_id}/manifest")
def answer_card_manifest(paper_id: UUID, artifact_id: UUID, db: Session = Depends(get_db)):
    return artifacts.get_artifact(db, paper_id, artifact_id).manifest


@router.get("/paper-instances/{paper_id}/answer-card-artifacts/{artifact_id}/pdf")
def answer_card_pdf(paper_id: UUID, artifact_id: UUID, db: Session = Depends(get_db)):
    artifact = artifacts.get_artifact(db, paper_id, artifact_id)
    return Response(artifacts.artifact_pdf(artifact), media_type="application/pdf", headers={
        "Content-Disposition": f'attachment; filename="answer-card-{artifact.artifact_code}.pdf"',
        "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff",
        "ETag": f'"{artifact.pdf_sha256}"',
    })


@router.get("/paper-instances/{paper_id}/answer-card-artifacts/{artifact_id}/pages/{page_no}/image")
def answer_card_page_image(paper_id: UUID, artifact_id: UUID, page_no: int, db: Session = Depends(get_db)):
    artifact = artifacts.get_artifact(db, paper_id, artifact_id)
    return Response(artifacts.artifact_page_svg(artifact, page_no), media_type="image/svg+xml", headers={
        "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'",
    })


@router.post("/equivalence-preview")
def preview(request: CreateAssessmentTask, db: Session = Depends(get_db)):
    return service.create_task(db, request, preview_only=True)


_PAGE_COLLECTION = "/paper-instances/{paper_id}/answer-card-artifacts/{artifact_id}/page-collection"


@router.post(_PAGE_COLLECTION)
def create_page_collection(paper_id: UUID, artifact_id: UUID, request: CreatePageCollection, db: Session = Depends(get_db)):
    return answer_pages.create_collection(db, paper_id, artifact_id, request)


@router.get(_PAGE_COLLECTION)
def read_page_collection(paper_id: UUID, artifact_id: UUID, db: Session = Depends(get_db)):
    return answer_pages.collection_view(db, paper_id, artifact_id)


@router.post(_PAGE_COLLECTION + "/pages/{page_no}/images")
def upload_page_image(paper_id: UUID, artifact_id: UUID, page_no: int,
                      file: UploadFile = File(...), expected_revision_no: int = Form(..., ge=0),
                      reviewer: str = Form(..., min_length=1, max_length=100),
                      reason: str = Form(..., min_length=2, max_length=500), db: Session = Depends(get_db)):
    # Sync route runs decoding/DB in FastAPI's thread pool. Bounded read prevents
    # an oversized multipart file from being loaded into application memory.
    service.enabled()
    if not reviewer.strip() or len(reason.strip()) < 2:
        raise service.AssessmentProblem("INVALID_AUDIT_LABEL", "请填写核对人及至少两个字的操作原因", 422)
    data = file.file.read(get_settings().recognition_max_upload_bytes + 1)
    request = SelectPageImage(expected_revision_no=expected_revision_no, image_id=None,
                              reviewer=reviewer, reason=reason)
    return answer_pages.upload_image(db, paper_id, artifact_id, page_no, request, data)


@router.put(_PAGE_COLLECTION + "/pages/{page_no}/effective-image")
def select_page_image(paper_id: UUID, artifact_id: UUID, page_no: int,
                      request: SelectPageImage, db: Session = Depends(get_db)):
    return answer_pages.select_image(db, paper_id, artifact_id, page_no, request)


@router.get(_PAGE_COLLECTION + "/images/{image_id}")
def read_page_image(paper_id: UUID, artifact_id: UUID, image_id: UUID, db: Session = Depends(get_db)):
    return Response(answer_pages.read_image(db, paper_id, artifact_id, image_id), media_type="image/jpeg",
                    headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})


@router.get(_PAGE_COLLECTION + "/vision-tasks")
def list_page_vision_tasks(paper_id: UUID, artifact_id: UUID, db: Session = Depends(get_db)):
    return vision_pages.list_tasks(db, paper_id, artifact_id)


@router.post(_PAGE_COLLECTION + "/vision-tasks")
def create_page_vision_tasks(paper_id: UUID, artifact_id: UUID, request: CreatePageVisionTasks, db: Session = Depends(get_db)):
    return vision_pages.create_tasks(db, paper_id, artifact_id, request)


@router.get(_PAGE_COLLECTION + "/vision-tasks/{task_id}")
def read_page_vision_task(paper_id: UUID, artifact_id: UUID, task_id: UUID, db: Session = Depends(get_db)):
    return vision_pages.task_detail(db, paper_id, artifact_id, task_id)


@router.post(_PAGE_COLLECTION + "/vision-tasks/{task_id}/{action}")
def act_on_page_vision_task(paper_id: UUID, artifact_id: UUID, task_id: UUID,
                          action: Literal["retry", "cancel", "rerun"], request: PageVisionAction, db: Session = Depends(get_db)):
    return vision_pages.task_action(db, paper_id, artifact_id, task_id, action, request)


@router.post("/equivalence-tasks")
def create(request: CreateAssessmentTask, db: Session = Depends(get_db)):
    task, reused = service.create_task(db, request)
    return {**service.task_view(db, task), "reused": reused}


@router.get("/equivalence-tasks")
def list_tasks(paper_instance_item_id: UUID, db: Session = Depends(get_db)):
    # Scope is mandatory; no unbounded all-student dump.
    service._paper_item(db, paper_instance_item_id, active=False)
    ids = db.scalars(select(AssessmentTask.id).where(
        AssessmentTask.snapshot["paper_instance_item_id"].astext == str(paper_instance_item_id),
    ).order_by(AssessmentTask.created_at.desc(), AssessmentTask.id.desc()).limit(50)).all()
    return [service.task_view(db, service.get_task(db, task_id)) for task_id in ids]


@router.get("/equivalence-tasks/{task_id}")
def detail(task_id: UUID, db: Session = Depends(get_db)):
    return service.task_view(db, service.get_task(db, task_id))


@router.post("/equivalence-tasks/{task_id}/{action}")
def action(task_id: UUID, action: Literal["retry", "cancel", "rerun"], request: TaskAction, db: Session = Depends(get_db)):
    return service.task_view(db, service.task_action(db, task_id, action, request.reason))


@router.get("/quality-feedback/{target_kind}/{target_id}/{item_id}")
def feedback(target_kind: Literal["equivalence", "recognition"], target_id: UUID, item_id: str, db: Session = Depends(get_db)):
    return service.quality_feedback(db, target_kind, target_id, item_id)


@router.put("/quality-feedback/{target_kind}/{target_id}/{item_id}")
def save_feedback(target_kind: Literal["equivalence", "recognition"], target_id: UUID, item_id: str, request: QualityFeedbackIn, db: Session = Depends(get_db)):
    return service.quality_feedback(db, target_kind, target_id, item_id, request)
