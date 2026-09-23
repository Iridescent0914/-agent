from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models import Attempt, Student
from app.schemas.common import Page
from app.schemas.students import StudentAttemptOut, StudentDetail, StudentListItem
from app.services.student_profile import attempt_summary, load_context


router = APIRouter(prefix="/students", tags=["students"])


def _student_stats_subquery():
    return (
        select(
            Attempt.student_id.label("student_id"),
            func.count(Attempt.id).label("attempt_count"),
            func.count(Attempt.id).filter(Attempt.is_correct.is_(True)).label("correct_count"),
            func.max(Attempt.answered_at).label("latest_answered_at"),
        )
        .group_by(Attempt.student_id)
        .subquery()
    )


def _student_item(student: Student, row: object) -> StudentListItem:
    return StudentListItem(
        id=student.id,
        student_code=student.student_code,
        display_name=student.display_name,
        class_name=student.class_name,
        is_demo=student.is_demo,
        attempt_count=int(getattr(row, "attempt_count") or 0),
        correct_count=int(getattr(row, "correct_count") or 0),
        latest_answered_at=getattr(row, "latest_answered_at"),
    )


@router.get("", response_model=Page[StudentListItem])
def list_students(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
) -> Page[StudentListItem]:
    stats = _student_stats_subquery()
    total = db.scalar(select(func.count(Student.id))) or 0
    rows = db.execute(
        select(
            Student,
            stats.c.attempt_count,
            stats.c.correct_count,
            stats.c.latest_answered_at,
        )
        .outerjoin(stats, stats.c.student_id == Student.id)
        .order_by(Student.student_code, Student.id)
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return Page(
        items=[_student_item(row.Student, row) for row in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/{student_id}", response_model=StudentDetail)
def get_student(student_id: uuid.UUID, db: Session = Depends(get_db)) -> StudentDetail:
    stats = _student_stats_subquery()
    row = db.execute(
        select(
            Student,
            stats.c.attempt_count,
            stats.c.correct_count,
            stats.c.latest_answered_at,
        )
        .outerjoin(stats, stats.c.student_id == Student.id)
        .where(Student.id == student_id)
    ).one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="student not found")
    item = _student_item(row.Student, row)
    return StudentDetail(**item.model_dump(), created_at=row.Student.created_at)


@router.get("/{student_id}/attempts", response_model=Page[StudentAttemptOut])
def list_student_attempts(
    student_id: uuid.UUID,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    is_correct: bool | None = None,
    exam_point_code: str | None = None,
    db: Session = Depends(get_db),
) -> Page[StudentAttemptOut]:
    if db.get(Student, student_id) is None:
        raise HTTPException(status_code=404, detail="student not found")

    context = load_context(db, student_id)
    items = [attempt_summary(record) for record in reversed(context.records)]
    if is_correct is not None:
        items = [item for item in items if item.is_correct is is_correct]
    if exam_point_code is not None:
        items = [item for item in items if item.main_exam_point_code == exam_point_code]
    start = (page - 1) * page_size
    return Page(items=items[start:start + page_size], total=len(items), page=page, page_size=page_size)
