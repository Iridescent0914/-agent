"""Internal suggestion endpoints; reviewer is an audit label, not authentication."""
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import get_db
from app.models import AssessmentTask
from app.schemas.assessment import CreateAssessmentTask, QualityFeedbackIn, TaskAction
from app.services import assessment as service


def internal_only():
    # Until trusted teacher identities/ownership are available, fail closed in production.
    if get_settings().app_env not in {"development", "test"}:
        raise service.AssessmentProblem("MODEL_DISABLED", "S1入口仅供内部开发使用", 403)


router = APIRouter(prefix="/internal/stage6", tags=["stage6-internal"], dependencies=[Depends(internal_only)])


@router.get("/status")
def status():
    return {"enabled": get_settings().stage6_enabled, "provider": "fake",
            "policy_version": service.POLICY_VERSION, "real_provider_available": False}


@router.post("/equivalence-preview")
def preview(request: CreateAssessmentTask, db: Session = Depends(get_db)):
    return service.create_task(db, request, preview_only=True)


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
