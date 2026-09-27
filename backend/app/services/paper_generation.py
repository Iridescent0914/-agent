from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Iterable
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Assignment, PaperInstance, Student
from app.schemas.assignments import (
    AssignmentCreateRequest, AssignmentPlanRequest, AssignmentPreviewOut,
    StudentRecommendationPreview,
)
from app.services.paper_snapshots import load_question, new_item, snapshot_payload
from app.services.recommendations import preview
from app.services.student_profile import load_context


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


def preview_assignment(db: Session, request: AssignmentPlanRequest, *,
                       as_of: datetime | None = None) -> AssignmentPreviewOut:
    at = as_of or datetime.now(timezone.utc)
    students = _students(db, request.student_ids)
    results = []
    for student in students:
        recommendation = preview(
            db, load_context(db, student.id), request.recommendation_parameters, as_of=at
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
        student_ids=request.student_ids,
        recommendation_parameters=request.recommendation_parameters,
    )
    result = preview_assignment(db, plan, as_of=as_of)
    params = request.recommendation_parameters.model_dump(mode="json")
    assignment = Assignment(
        idempotency_key=request.idempotency_key,
        title=request.title,
        chapter_code=request.recommendation_parameters.chapter_code,
        teaching_scope={
            "new_exam_point_codes": params["new_exam_point_codes"],
            "review_exam_point_codes": params["review_exam_point_codes"],
        },
        generation_config={
            "schema_version": "m3-generation-config-v1",
            "request_fingerprint": fingerprint,
            "as_of": result.as_of.isoformat(),
            "student_ids": [str(value) for value in request.student_ids],
            "recommendation_parameters": params,
        },
        assignment_type="practice",
    )
    db.add(assignment)
    db.flush()
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
            question = load_question(db, selected.question_id)
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
