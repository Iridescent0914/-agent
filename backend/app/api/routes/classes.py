from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models import SchoolClass, Student
from app.schemas.classes import ClassCreateRequest, ClassOut, ClassUpdateRequest


router = APIRouter(prefix="/classes", tags=["classes"])


def _class_out(school_class: SchoolClass, student_count: int = 0) -> ClassOut:
    return ClassOut(
        id=school_class.id,
        name=school_class.name,
        grade=school_class.grade,
        school_year=school_class.school_year,
        teacher_name=school_class.teacher_name,
        student_count=student_count,
        created_at=school_class.created_at,
        updated_at=school_class.updated_at,
    )


@router.get("", response_model=list[ClassOut])
def list_school_classes(db: Session = Depends(get_db)) -> list[ClassOut]:
    rows = db.execute(
        select(SchoolClass, func.count(Student.id).label("student_count"))
        .outerjoin(
            Student,
            (Student.class_id == SchoolClass.id) & Student.archived_at.is_(None),
        )
        .group_by(SchoolClass.id)
        .order_by(SchoolClass.school_year.desc(), SchoolClass.grade, SchoolClass.name)
    ).all()
    return [_class_out(row.SchoolClass, int(row.student_count)) for row in rows]


@router.post("", response_model=ClassOut, status_code=201)
def create_school_class(
    request: ClassCreateRequest,
    db: Session = Depends(get_db),
) -> ClassOut:
    if db.scalar(select(SchoolClass.id).where(SchoolClass.name == request.name)):
        raise HTTPException(status_code=409, detail="班级名称已存在")
    school_class = SchoolClass(**request.model_dump())
    db.add(school_class)
    db.flush()
    return _class_out(school_class)


@router.patch("/{class_id}", response_model=ClassOut)
def update_school_class(
    class_id: uuid.UUID,
    request: ClassUpdateRequest,
    db: Session = Depends(get_db),
) -> ClassOut:
    school_class = db.scalar(
        select(SchoolClass).where(SchoolClass.id == class_id).with_for_update()
    )
    if school_class is None:
        raise HTTPException(status_code=404, detail="班级不存在")

    changes = request.model_dump(exclude_unset=True)
    new_name = changes.get("name")
    if new_name and new_name != school_class.name:
        duplicate = db.scalar(
            select(SchoolClass.id).where(
                SchoolClass.name == new_name,
                SchoolClass.id != class_id,
            )
        )
        if duplicate:
            raise HTTPException(status_code=409, detail="班级名称已存在")
        old_name = school_class.name
        db.execute(
            update(Student)
            .where((Student.class_id == class_id) | (Student.class_name == old_name))
            .values(class_id=class_id, class_name=new_name)
        )

    for field, value in changes.items():
        setattr(school_class, field, value)
    db.flush()
    active_count = db.scalar(
        select(func.count(Student.id)).where(
            Student.class_id == class_id,
            Student.archived_at.is_(None),
        )
    ) or 0
    return _class_out(school_class, int(active_count))


@router.delete("/{class_id}", status_code=204)
def delete_school_class(class_id: uuid.UUID, db: Session = Depends(get_db)) -> None:
    school_class = db.scalar(
        select(SchoolClass).where(SchoolClass.id == class_id).with_for_update()
    )
    if school_class is None:
        raise HTTPException(status_code=404, detail="班级不存在")
    active_count = db.scalar(
        select(func.count(Student.id)).where(
            ((Student.class_id == class_id) | (Student.class_name == school_class.name)),
            Student.archived_at.is_(None),
        )
    ) or 0
    if active_count:
        raise HTTPException(status_code=409, detail="班级中仍有学生，请先转移或删除学生")
    db.delete(school_class)
