from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Iterable
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Assignment, PaperInstance, SchoolClass, Student
from app.schemas.assignments import (
    AssignmentCreateRequest, AssignmentPlanRequest, AssignmentPreviewOut,
    StudentRecommendationPreview,
)
from app.services.paper_snapshots import load_questions, new_item, snapshot_payload
from app.services.recommendations import preview, recommendation_candidates
from app.services.student_profile import load_contexts


def _students(db: Session, student_ids: Iterable[UUID]) -> list[Student]:
    ids = list(student_ids)
    found = list(db.scalars(select(Student).where(
        Student.id.in_(ids),
        Student.archived_at.is_(None),
    )))
    if len(found) != len(ids):
        raise LookupError("one or more students not found")
    by_id = {student.id: student for student in found}
    return [by_id[student_id] for student_id in ids]


def _students_for_request(
    db: Session, request: AssignmentPlanRequest
) -> list[Student]:
    if request.student_scope == "selected":
        return _students(db, request.student_ids)
    students = list(db.scalars(
        select(Student).where(
            Student.class_id == request.class_id,
            Student.archived_at.is_(None),
        ).order_by(Student.student_code, Student.id)
    ))
    if not students:
        raise LookupError("class has no active students")
    return students


def _school_class(
    db: Session, students: list[Student], requested_class_id: UUID | None
) -> SchoolClass:
    if requested_class_id is not None:
        school_class = db.get(SchoolClass, requested_class_id)
        if school_class is None:
            raise LookupError("class not found")
        if any(
            student.class_id != school_class.id and student.class_name != school_class.name
            for student in students
        ):
            raise ValueError("所选学生中包含不属于该班级的学生")
        return school_class

    class_ids = {student.class_id for student in students if student.class_id is not None}
    class_names = {student.class_name for student in students}
    if len(class_ids) > 1 or len(class_names) > 1:
        raise ValueError("一次作业只能选择同一班级的学生")
    school_class = db.get(SchoolClass, next(iter(class_ids))) if class_ids else None
    if school_class is None:
        class_name = next(iter(class_names))
        school_class = db.scalar(select(SchoolClass).where(SchoolClass.name == class_name))
    if school_class is None:
        raise LookupError("class not found")
    return school_class


def preview_assignment(db: Session, request: AssignmentPlanRequest, *,
                       as_of: datetime | None = None) -> AssignmentPreviewOut:
    at = as_of or datetime.now(timezone.utc)
    students = _students_for_request(db, request)
    _school_class(db, students, request.class_id)
    contexts = load_contexts(db, [student.id for student in students])
    candidates = recommendation_candidates(
        db, request.recommendation_parameters.chapter_code
    )
    results = []
    for student in students:
        recommendation = preview(
            db, contexts[student.id], request.recommendation_parameters,
            as_of=at, candidates=candidates,
        )
        results.append(StudentRecommendationPreview(
            student_id=student.id, student_code=student.student_code,
            student_name=student.display_name, recommendation=recommendation,
        ))
    return AssignmentPreviewOut(
        as_of=at,
        student_count=len(results),
        ready_count=sum(item.recommendation.shortfall == 0 for item in results),
        shortfall_student_count=sum(item.recommendation.shortfall > 0 for item in results),
        students=results,
    )


def request_fingerprint(request: AssignmentCreateRequest) -> str:
    payload = request.model_dump(mode="json", exclude={"idempotency_key"})
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def create_assignment(db: Session, request: AssignmentCreateRequest, *,
                      as_of: datetime | None = None) -> tuple[Assignment, bool]:
    fingerprint = request_fingerprint(request)
    existing = db.scalar(select(Assignment).where(Assignment.idempotency_key == request.idempotency_key))
    if existing is not None:
        if (existing.generation_config or {}).get("request_fingerprint") != fingerprint:
            raise RuntimeError("idempotency key was already used for another request")
        return existing, False

    plan = AssignmentPlanRequest(
        class_id=request.class_id,
        student_scope=request.student_scope,
        student_ids=request.student_ids,
        recommendation_parameters=request.recommendation_parameters,
    )
    result = preview_assignment(db, plan, as_of=as_of)
    students = _students_for_request(db, request)
    school_class = _school_class(db, students, request.class_id)
    params = request.recommendation_parameters.model_dump(mode="json")
    assignment = Assignment(
        idempotency_key=request.idempotency_key,
        title=request.title,
        class_id=school_class.id,
        chapter_code=request.recommendation_parameters.chapter_code,
        teaching_scope={
            "new_exam_point_codes": params["new_exam_point_codes"],
            "review_exam_point_codes": params["review_exam_point_codes"],
        },
        generation_config={
            "schema_version": "m3-generation-config-v1",
            "request_fingerprint": fingerprint,
            "as_of": result.as_of.isoformat(),
            "student_scope": request.student_scope,
            "student_ids": [str(value) for value in request.student_ids],
            "resolved_student_ids": [str(value.student_id) for value in result.students],
            "recommendation_parameters": params,
        },
        assignment_type="practice",
    )
    db.add(assignment)
    db.flush()
    questions = load_questions(db, {
        selected.question_id
        for student_result in result.students
        for selected in student_result.recommendation.items
    })
    for student_result in result.students:
        output = student_result.recommendation
        paper = PaperInstance(
            assignment_id=assignment.id,
            student_id=student_result.student_id,
            version_no=1,
            status="draft",
            recommendation_audit={
                "schema_version": "m3-recommendation-audit-v1",
                **output.model_dump(mode="json"),
            },
            edit_history=[],
            edit_revision=1,
        )
        db.add(paper)
        db.flush()
        for order_no, selected in enumerate(output.items, start=1):
            question = questions[selected.question_id]
            audit = {
                "schema_version": "m3-selection-audit-v1",
                "source": "recommendation",
                "selected_at": result.as_of.isoformat(),
                **selected.model_dump(mode="json"),
            }
            db.add(new_item(
                paper_id=paper.id, order_no=order_no,
                payload=snapshot_payload(question), selection_audit=audit,
            ))
    db.flush()
    return assignment, True
