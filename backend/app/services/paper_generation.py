from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Iterable
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Assignment,
    KnowledgePoint,
    PaperInstance,
    PaperInstanceItem,
    SchoolClass,
    Student,
)
from app.schemas.assignments import (
    AssignmentCreateRequest, AssignmentPlanRequest, AssignmentPreviewOut,
    StudentRecommendationPreview,
)
from app.schemas.recommendations import (
    PersonalizedDifficultyDiagnosticOut,
    PersonalizedItemAudit,
    PersonalizedSelectionPreviewOut,
    RecommendedQuestion,
    ScoreParts,
)
from app.services.paper_snapshots import load_questions, new_item, snapshot_payload
from app.services.personalized_preview import preview_personalized_questions
from app.services.personalized_selection import PersonalizedSelectionResult
from app.services.recommendations import (
    history_recommendation_candidates, history_source_question_ids,
    history_wrong_answers, preview,
    recommendation_candidates,
    required_question_candidates, select_history_recommendations,
    select_required_recommendations,
)
from app.services.student_profile import load_contexts


ZERO_SCORE_PARTS = ScoreParts(
    weakness=0,
    recent_wrong=0,
    difficulty_match=0,
    forgetting=0,
)


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


def _personalized_preview_out(
    selection: PersonalizedSelectionResult,
    *,
    rule_version: str,
    usage_counts: dict[UUID, int],
) -> PersonalizedSelectionPreviewOut:
    return PersonalizedSelectionPreviewOut(
        literacy_name=selection.literacy_name,
        literacy_score=selection.literacy_score,
        score_source="computed",
        rule_version=rule_version,
        base_star=selection.base_star,
        target_stars=list(selection.target_stars),
        requested_count=3,
        selected_count=selection.selected_count,
        shortfall=selection.shortfall,
        status=selection.status,
        diagnostics=[
            PersonalizedDifficultyDiagnosticOut(
                difficulty=item.difficulty,
                requested_count=item.requested_count,
                exact_candidate_count=item.exact_candidate_count,
                ready_candidate_count=item.ready_candidate_count,
                available_candidate_count=item.available_candidate_count,
                selected_count=item.selected_count,
                shortfall=item.shortfall,
                reason_codes=list(item.reason_codes),
            )
            for item in selection.diagnostics
        ],
        reused_question_count=sum(
            usage_counts.get(question_id, 0) > 1
            for question_id in selection.selected_question_ids
        ),
        max_class_usage_count=max(
            (
                usage_counts.get(question_id, 1)
                for question_id in selection.selected_question_ids
            ),
            default=1,
        ),
    )


def _personalized_items(
    selection: PersonalizedSelectionResult,
    *,
    as_of: datetime,
    rule_version: str,
    usage_counts: dict[UUID, int],
) -> list[RecommendedQuestion]:
    result: list[RecommendedQuestion] = []
    for slot in selection.slots:
        candidate = slot.candidate
        if candidate is None:
            continue
        audit = PersonalizedItemAudit(
            slot_no=slot.slot_no,
            literacy_name=selection.literacy_name,
            literacy_score=selection.literacy_score,
            score_source="computed",
            rule_version=rule_version,
            base_star=selection.base_star,
            target_star=slot.target_star,
            target_stars=list(selection.target_stars),
            generated_at=as_of,
            class_usage_count=usage_counts.get(candidate.question_id, 1),
        )
        result.append(RecommendedQuestion(
            question_id=candidate.question_id,
            original_qid=candidate.original_qid,
            question_type=candidate.question_type,
            difficulty=candidate.difficulty,
            knowledge_point_code=candidate.knowledge_point_code,
            knowledge_point_name=candidate.knowledge_point_name,
            bucket="new",
            strategy="personalized",
            score=1,
            components=ZERO_SCORE_PARTS,
            contributions=ZERO_SCORE_PARTS,
            target_difficulty=slot.target_star,
            reason_codes=[
                "personalized_literacy_score",
                "teacher_knowledge_scope",
                "exact_target_difficulty",
            ],
            reason_text=(
                f"{selection.literacy_name}当前整数分 {selection.literacy_score}，"
                f"基础 {selection.base_star} 星；第 {slot.slot_no} 题按"
                f" {slot.target_star} 星精确选题。"
            ),
            evidence=[],
            source_module="personalized",
            personalized_audit=audit,
        ))
    return result


def preview_assignment(db: Session, request: AssignmentPlanRequest, *,
                       as_of: datetime | None = None) -> AssignmentPreviewOut:
    at = as_of or datetime.now(timezone.utc)
    students = _students_for_request(db, request)
    school_class = _school_class(db, students, request.class_id)
    _validate_personalized_filter(db, request)
    if request.filter_config is not None:
        required = request.filter_config.required
        chapter_codes = request.filter_config.chapter_codes
        # In the stage-two contract an empty required scope means "all
        # knowledge points in the selected grades and chapters".  Do not let
        # the legacy recommendation field silently narrow that scope.
        scope_codes = list(required.knowledge_point_codes)
        candidates = required_question_candidates(
            db, chapter_codes, request.filter_config.grade_codes,
            scope_codes, required.core_literacies,
        )
        effective_scope_codes = scope_codes or sorted({
            candidate.knowledge_point_code for candidate in candidates
        })
        difficulty_counts = {
            str(difficulty): count
            for difficulty, count in required.difficulty_counts.items()
            if count
        }
        total_required = sum(difficulty_counts.values())
        params = request.recommendation_parameters.model_copy(update={
            "new_count": total_required, "review_count": 0,
            "new_knowledge_point_codes": effective_scope_codes,
            "review_knowledge_point_codes": [],
        })
        template = select_required_recommendations(
            student_id=students[0].id, candidates=candidates,
            knowledge_point_codes=effective_scope_codes,
            difficulty_counts=difficulty_counts, params=params, as_of=at,
        )
        history_errors = {student.id: [] for student in students}
        history_source_ids = {student.id: set() for student in students}
        history_candidates = []
        if request.filter_config.history.assignment_id is not None:
            source_assignment_id = request.filter_config.history.assignment_id
            history_errors = history_wrong_answers(
                db,
                source_assignment_id=source_assignment_id,
                class_id=school_class.id,
                student_ids=[student.id for student in students],
            )
            history_source_ids = history_source_question_ids(
                db,
                source_assignment_id=source_assignment_id,
                student_ids=[student.id for student in students],
            )
            history_knowledge_point_codes = sorted({
                error.knowledge_point_code
                for errors in history_errors.values()
                for error in errors
            })
            history_candidates = history_recommendation_candidates(
                db, history_knowledge_point_codes,
            )
        required_ids = {item.question_id for item in template.items}
        history_by_student = {}
        personalized_exclusions = {}
        profile_contexts = (
            load_contexts(db, [student.id for student in students])
            if params.avoid_recent_days
            else {}
        )
        recently_assigned_by_student: dict[UUID, set[UUID]] = {
            student.id: set() for student in students
        }
        if params.avoid_recent_days:
            recent_cutoff = at - timedelta(days=params.avoid_recent_days)
            for student_id, question_id in db.execute(
                select(PaperInstance.student_id, PaperInstanceItem.question_id)
                .join(Assignment, Assignment.id == PaperInstance.assignment_id)
                .join(
                    PaperInstanceItem,
                    PaperInstanceItem.paper_instance_id == PaperInstance.id,
                )
                .where(
                    PaperInstance.student_id.in_([student.id for student in students]),
                    Assignment.created_at >= recent_cutoff,
                    Assignment.created_at <= at,
                )
            ):
                recently_assigned_by_student[student_id].add(question_id)
        for student in students:
            wrong_answers = history_errors[student.id]
            history_items, history_diagnostic = select_history_recommendations(
                student_id=student.id,
                candidates=history_candidates,
                errors=wrong_answers,
                excluded_question_ids=(
                    required_ids
                    | history_source_ids[student.id]
                ),
                params=params,
                as_of=at,
            )
            history_by_student[student.id] = (
                wrong_answers,
                history_items,
                history_diagnostic,
            )
            recent_practiced_ids: set[UUID] = set()
            if params.avoid_recent_days:
                recent_cutoff = at - timedelta(days=params.avoid_recent_days)
                recent_practiced_ids = {
                    record.attempt.question_id
                    for record in profile_contexts[student.id].records
                    if recent_cutoff <= record.attempt.answered_at <= at
                }
            personalized_exclusions[student.id] = (
                required_ids
                | {item.question_id for item in history_items}
                | recent_practiced_ids
                | recently_assigned_by_student[student.id]
            )

        personalized_filter = request.filter_config.personalized
        personalized_batch = preview_personalized_questions(
            db,
            student_ids=[student.id for student in students],
            grade_codes=request.filter_config.grade_codes,
            chapter_codes=chapter_codes,
            knowledge_point_codes=personalized_filter.knowledge_point_codes,
            literacy_name=personalized_filter.primary_competency or "",
            excluded_question_ids=personalized_exclusions,
            as_of=at,
        )
        personalized_by_student = {
            item.student_id: item.selection
            for item in personalized_batch.students
        }

        results = []
        for student in students:
            wrong_answers, history_items, history_diagnostic = history_by_student[
                student.id
            ]
            personalized_selection = personalized_by_student[student.id]
            personalized_items = _personalized_items(
                personalized_selection,
                as_of=at,
                rule_version=personalized_batch.rule_version,
                usage_counts=dict(personalized_batch.question_usage_counts),
            )
            required_diagnostics = [
                diagnostic for diagnostic in template.diagnostics
                if diagnostic.bucket != "review"
            ]
            all_items = [*template.items, *history_items, *personalized_items]
            selected_per_knowledge = dict(template.selected_per_knowledge_point)
            for item in [*history_items, *personalized_items]:
                selected_per_knowledge[item.knowledge_point_code] = (
                    selected_per_knowledge.get(item.knowledge_point_code, 0) + 1
                )
            requested_count = template.requested_count + len(wrong_answers) + 3
            selected_count = len(all_items)
            shortfall = (
                template.new_shortfall
                + history_diagnostic.shortfall
                + personalized_selection.shortfall
            )
            recommendation = template.model_copy(update={
                "student_id": student.id,
                "status": "ready" if shortfall == 0 else "insufficient_candidates",
                "requested_count": requested_count,
                "selected_count": selected_count,
                "shortfall": shortfall,
                "review_shortfall": history_diagnostic.shortfall,
                "personalized_shortfall": personalized_selection.shortfall,
                "selected_per_knowledge_point": selected_per_knowledge,
                "diagnostics": [*required_diagnostics, history_diagnostic],
                "items": all_items,
            })
            results.append(StudentRecommendationPreview(
                student_id=student.id,
                student_code=student.student_code,
                student_name=student.display_name,
                recommendation=recommendation,
                personalized=_personalized_preview_out(
                    personalized_selection,
                    rule_version=personalized_batch.rule_version,
                    usage_counts=dict(personalized_batch.question_usage_counts),
                ),
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
        personalized_reused_question_count=(
            personalized_batch.reused_question_count
            if request.filter_config is not None
            else 0
        ),
        personalized_max_class_usage_count=(
            personalized_batch.max_class_usage_count
            if request.filter_config is not None
            else 1
        ),
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
    personalized_shortfalls = [
        student
        for student in result.students
        if student.personalized is not None and student.personalized.shortfall > 0
    ]
    if personalized_shortfalls:
        sample = "、".join(
            student.student_code for student in personalized_shortfalls[:5]
        )
        first = personalized_shortfalls[0]
        missing = "、".join(
            f"{item.difficulty}星缺{item.shortfall}道"
            for item in first.personalized.diagnostics
            if item.shortfall > 0
        )
        suffix = "等" if len(personalized_shortfalls) > 5 else ""
        raise ValueError(
            f"个性化题库存不足：{len(personalized_shortfalls)} 名学生无法生成完整三题"
            f"（{sample}{suffix}）；{first.student_code}：{missing}。"
            "请补充同时匹配主要素养、目标星级且可机判已审核的题目，"
            "或调整知识点范围；近期已布置或作答题也会按避免重复天数排除。"
        )
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
            "personalized_score_policy": {
                "score_source": "computed",
                "score_type": "rounded_integer",
                "rule_version": (
                    result.students[0].personalized.rule_version
                    if result.students and result.students[0].personalized is not None
                    else None
                ),
                "recomputed_at_creation": True,
            },
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
                "personalized": (
                    student_result.personalized.model_dump(mode="json")
                    if student_result.personalized is not None
                    else None
                ),
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
