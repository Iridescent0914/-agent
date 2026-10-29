from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.schemas.analytics import (
    AssignmentAnalyticsOut, ClassOption, ClassOverviewOut,
    KnowledgePointStudentListOut,
)
from app.services.class_analytics import (
    AnalyticsDataError,
    build_assignment_overview,
    build_class_overview,
    build_knowledge_point_students,
    list_classes,
)

router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.get("/classes", response_model=list[ClassOption])
def get_classes(db: Session = Depends(get_db)):
    return list_classes(db)


@router.get("/class-overview", response_model=ClassOverviewOut)
def get_class_overview(
    class_name: str = Query(min_length=1, max_length=120),
    chapter_code: str = Query(min_length=1, max_length=160),
    grade: str | None = Query(None, min_length=1, max_length=80),
    db: Session = Depends(get_db),
):
    try:
        return build_class_overview(
            db, class_name=class_name, chapter_code=chapter_code, grade=grade
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get(
    "/classes/knowledge-points/{knowledge_point_code}/students",
    response_model=KnowledgePointStudentListOut,
)
def get_knowledge_point_students(
    knowledge_point_code: str,
    class_name: str = Query(min_length=1, max_length=120),
    chapter_code: str = Query(min_length=1, max_length=160),
    db: Session = Depends(get_db),
):
    try:
        return build_knowledge_point_students(
            db, class_name=class_name, chapter_code=chapter_code,
            knowledge_point_code=knowledge_point_code,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/assignments/{assignment_id}", response_model=AssignmentAnalyticsOut)
def get_assignment_analytics(
    assignment_id: UUID,
    class_name: str | None = Query(None, min_length=1, max_length=120),
    db: Session = Depends(get_db),
):
    try:
        return build_assignment_overview(db, assignment_id, class_name=class_name)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except AnalyticsDataError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
