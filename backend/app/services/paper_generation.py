from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Iterable
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Assignment, KnowledgePoint, PaperInstance, SchoolClass, Student
from app.schemas.assignments import (
    AssignmentCreateRequest, AssignmentPlanRequest, AssignmentPreviewOut,
    StudentRecommendationPreview,
)
from app.services.paper_snapshots import load_questions, new_item, snapshot_payload
from app.services.recommendations import (
    history_wrong_answers, preview, recommendation_candidates,
    required_question_candidates, select_history_recommendations,
    select_required_recommendations,
)
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


def _validate_personalized_filter(db: Session, request: AssignmentPlanRequest) -> None:
    if request.filter_config is None:
        return
    personalized = request.filter_config.personalized
    rows = list(db.scalars(select(KnowledgePoint).where(
        KnowledgePoint.code.in_(personalized.knowledge_point_codes),
    )))
    by_code = {item.code: item for item in rows}
    if set(by_code) != set(personalized.knowledge_point_codes):
        raise ValueError("个性化知识点不存在")
    if any(
        item.chapter_code not in request.filter_config.chapter_codes
        for item in rows
    ):
        raise ValueError("个性化知识点不属于所选章节")
    allowed_competencies = {
        competency
        for item in rows
        for competency in (item.core_competencies or [])
    }
    if personalized.primary_competency not in allowed_competencies:
        raise ValueError("主要能力不属于所选知识点")


def preview_assignment(db: Session, request: AssignmentPlanRequest, *,
                       as_of: datetime | None = None) -> AssignmentPreviewOut:
    at = as_of or datetime.now(timezone.utc)
    students = _students_for_request(db, request)
    school_class = _school_class(db, students, request.class_id)
    _validate_personalized_filter(db, request)
    if request.filter_config is not None:
        required = request.filter_config.required
        chapter_codes = request.filter_config.chapter_codes
        scope_codes = required.knowledge_point_codes or request.recommendation_parameters.new_knowledge_point_codes
        candidates = required_question_candidates(
            db, chapter_codes, request.filter_config.grade_codes,
            scope_codes, required.core_literacies,
        )
        difficulty_counts = {
            str(difficulty): count
            for difficulty, count in required.difficulty_counts.items()
            if count
        }
        total_required = sum(difficulty_counts.values())
        params = request.recommendation_parameters
        if params.new_count != total_required or params.review_count != 0:
            params = params.model_copy(update={
                "new_count": total_required, "review_count": 0,
                "new_knowledge_point_codes": scope_codes,
                "review_knowledge_point_codes": [],
            })
        template = select_required_recommendations(
            student_id=students[0].id, candidates=candidates,
            knowledge_point_codes=scope_codes,
            difficulty_counts=difficulty_counts, params=params, as_of=at,
        )
        history_errors = {student.id: [] for student in students}
        history_candidates = []
        if request.filter_config.history.assignment_id is not None:
            history_errors = history_wrong_answers(
                db,
                source_assignment_id=request.filter_config.history.assignment_id,
                class_id=school_class.id,
                student_ids=[student.id for student in students],
            )
            history_candidates = recommendation_candidates(
                db, chapter_codes, request.filter_config.grade_codes,
            )
        required_ids = {item.question_id for item in template.items}
        results = []
        for student in students:
            wrong_answers = history_errors[student.id]
            history_items, history_diagnostic = select_history_recommendations(
                student_id=student.id,
                candidates=history_candidates,
                errors=wrong_answers,
                excluded_question_ids=(
                    required_ids
                    | {item.source_question_id for item in wrong_answers}
                ),
                params=params,
                as_of=at,
            )
            required_diagnostics = [
                diagnostic for diagnostic in template.diagnostics
                if diagnostic.bucket != "review"
            ]
            all_items = [*template.items, *history_items]
            selected_per_knowledge = dict(template.selected_per_knowledge_point)
            for item in history_items:
                selected_per_knowledge[item.knowledge_point_code] = (
                    selected_per_knowledge.get(item.knowledge_point_code, 0) + 1
                )
            requested_count = template.requested_count + len(wrong_answers)
            selected_count = len(all_items)
            shortfall = template.new_shortfall + history_diagnostic.shortfall
            recommendation = template.model_copy(update={
                "student_id": student.id,
                "status": "ready" if shortfall == 0 else "insufficient_candidates",
                "requested_count": requested_count,
                "selected_count": selected_count,
                "shortfall": shortfall,
                "review_shortfall": history_diagnostic.shortfall,
                "selected_per_knowledge_point": selected_per_knowledge,
                "diagnostics": [*required_diagnostics, history_diagnostic],
                "items": all_items,
            })
            results.append(StudentRecommendationPreview(
                student_id=student.id,
                student_code=student.student_code,
                student_name=student.display_name,
                recommendation=recommendation,
            ))
    else:
        contexts = load_contexts(db, [student.id for student in students])
        candidates = recommendation_candidates(
            db,
            request.recommendation_parameters.chapter_codes
            or [request.recommendation_parameters.chapter_code],
            request.recommendation_parameters.grade_codes,
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
        filter_config=request.filter_config,
    )
    result = preview_assignment(db, plan, as_of=as_of)
    students = _students_for_request(db, request)
    school_class = _school_class(db, students, request.class_id)
    params = (
        result.students[0].recommendation.parameters.model_dump(mode="json")
        if result.students else request.recommendation_parameters.model_dump(mode="json")
    )
    filter_config = (
        request.filter_config.model_dump(mode="json")
        if request.filter_config is not None
        else None
    )
    teaching_scope = {
        "new_knowledge_point_codes": params["new_knowledge_point_codes"],
        "review_knowledge_point_codes": params["review_knowledge_point_codes"],
    }
    if filter_config is not None:
        teaching_scope["filter_config"] = filter_config
    assignment = Assignment(
        idempotency_key=request.idempotency_key,
        title=request.title,
        class_id=school_class.id,
        chapter_code=request.recommendation_parameters.chapter_code,
        teaching_scope=teaching_scope,
        generation_config={
            "schema_version": "m3-generation-config-v1",
            "request_fingerprint": fingerprint,
            "as_of": result.as_of.isoformat(),
            "student_scope": request.student_scope,
            "student_ids": [str(value) for value in request.student_ids],
            "resolved_student_ids": [str(value.student_id) for value in result.students],
            "recommendation_parameters": params,
            "filter_config": filter_config,
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
                "source_module": selected.source_module,
                "selected_at": result.as_of.isoformat(),
                **selected.model_dump(mode="json"),
            }
            db.add(new_item(
                paper_id=paper.id, order_no=order_no,
                payload=snapshot_payload(question), selection_audit=audit,
            ))
    db.flush()
    return assignment, True
