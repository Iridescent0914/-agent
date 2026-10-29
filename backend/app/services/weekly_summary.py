from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Assignment, Attempt, KnowledgePoint, PaperInstance, PaperInstanceItem,
    Question, SchoolClass, Student,
)
from app.schemas.profile import EvidenceRef
from app.schemas.recommendations import RecommendationRequest
from app.schemas.weekly_summary import (
    WeeklySummaryCreateRequest, WeeklySummaryKnowledgePointOut,
    WeeklySummaryPlanRequest, WeeklySummaryPreviewOut,
    WeeklySummaryStudentPreview,
)
from app.services.paper_snapshots import load_questions, new_item, snapshot_payload
from app.services.recommendations import (
    Candidate, history_source_question_ids, recent_completed_assignment_ids,
    score_candidate,
)


RULE_VERSION = "weekly-summary-v1"
FIXED_DIFFICULTY = 3
QUESTIONS_PER_KNOWLEDGE_POINT = 2
CONFIRMED_STATUSES = ("auto_confirmed", "teacher_confirmed")


def _students(db: Session, class_id: UUID) -> tuple[SchoolClass, list[Student]]:
    school_class = db.get(SchoolClass, class_id)
    if school_class is None:
        raise LookupError("班级不存在")
    students = list(db.scalars(select(Student).where(
        Student.class_id == class_id,
        Student.archived_at.is_(None),
    ).order_by(Student.student_code, Student.id)))
    if not students:
        raise LookupError("班级没有在读学生")
    return school_class, students


def _validate_sources(
    db: Session, class_id: UUID, source_assignment_ids: list[UUID],
) -> None:
    eligible = set(recent_completed_assignment_ids(db, class_id=class_id, limit=6))
    missing = [value for value in source_assignment_ids if value not in eligible]
    if missing:
        raise ValueError("周总结来源必须属于当前班级最近六次已全部批改作业")


def _false_knowledge_points(
    db: Session, *, source_assignment_ids: list[UUID], student_ids: list[UUID],
) -> dict[UUID, dict[UUID, dict]]:
    result: dict[UUID, dict[UUID, dict]] = {
        student_id: {} for student_id in student_ids
    }
    rows = db.execute(
        select(
            PaperInstance.student_id, PaperInstanceItem, Attempt,
            Question.main_knowledge_point_id, KnowledgePoint.code, KnowledgePoint.name,
        )
        .join(PaperInstanceItem, PaperInstanceItem.paper_instance_id == PaperInstance.id)
        .join(Attempt, Attempt.paper_instance_item_id == PaperInstanceItem.id)
        .join(Question, Question.id == PaperInstanceItem.question_id)
        .join(KnowledgePoint, KnowledgePoint.id == Question.main_knowledge_point_id)
        .where(
            PaperInstance.assignment_id.in_(source_assignment_ids),
            PaperInstance.student_id.in_(student_ids),
            PaperInstance.status == "published",
            PaperInstance.invalidated_at.is_(None),
            Attempt.grade_status.in_(CONFIRMED_STATUSES),
            Attempt.is_correct.is_(False),
        )
        .order_by(PaperInstance.student_id, Attempt.answered_at, Attempt.id)
    ).all()
    for student_id, item, attempt, knowledge_point_id, code, name in rows:
        if (item.selection_audit or {}).get("source_module") != "history":
            continue
        entry = result[student_id].setdefault(knowledge_point_id, {
            "code": code, "name": name, "evidence": [],
        })
        entry["evidence"].append(EvidenceRef(
            attempt_id=attempt.id, revision_no=attempt.revision_no,
        ))
    return result


def _candidates(db: Session, knowledge_point_ids: set[UUID]) -> dict[UUID, list[Candidate]]:
    grouped: dict[UUID, list[Candidate]] = defaultdict(list)
    if not knowledge_point_ids:
        return grouped
    rows = db.execute(
        select(Question, KnowledgePoint)
        .join(KnowledgePoint, KnowledgePoint.id == Question.main_knowledge_point_id)
        .where(
            Question.main_knowledge_point_id.in_(knowledge_point_ids),
            Question.difficulty == FIXED_DIFFICULTY,
            Question.archived_at.is_(None),
        )
    ).all()
    for question, knowledge_point in rows:
        if (
            question.grading_readiness != "machine_ready"
            or question.source_review_status != "reviewed"
        ):
            continue
        grouped[knowledge_point.id].append(Candidate(
            question_id=question.id,
            original_qid=question.original_qid,
            question_type=question.question_type,
            difficulty=question.difficulty,
            knowledge_point_id=knowledge_point.id,
            knowledge_point_code=knowledge_point.code,
            knowledge_point_name=knowledge_point.name,
        ))
    for values in grouped.values():
        values.sort(key=lambda item: (item.original_qid, item.question_id.int))
    return grouped


def _recommendation_parameters(
    db: Session, knowledge_point_ids: set[UUID], requested_count: int,
) -> RecommendationRequest | None:
    if not knowledge_point_ids:
        return None
    points = list(db.scalars(select(KnowledgePoint).where(
        KnowledgePoint.id.in_(knowledge_point_ids)
    ).order_by(KnowledgePoint.sort_order, KnowledgePoint.code)))
    codes = [item.code for item in points]
    chapters = list(dict.fromkeys(item.chapter_code for item in points))
    return RecommendationRequest(
        chapter_code=chapters[0],
        chapter_codes=chapters,
        review_knowledge_point_codes=codes,
        review_count=min(30, max(1, requested_count)),
        new_count=0,
        question_types=["choice", "fill_blank", "true_false"],
        base_difficulty=FIXED_DIFFICULTY,
        min_difficulty=FIXED_DIFFICULTY,
        max_difficulty=FIXED_DIFFICULTY,
        avoid_recent_days=0,
        max_knowledge_point_ratio=1,
    )


def preview_weekly_summary(
    db: Session, request: WeeklySummaryPlanRequest, *, as_of: datetime | None = None,
) -> WeeklySummaryPreviewOut:
    at = as_of or datetime.now(timezone.utc)
    _, students = _students(db, request.class_id)
    _validate_sources(db, request.class_id, request.source_assignment_ids)
    student_ids = [student.id for student in students]
    false_by_student = _false_knowledge_points(
        db,
        source_assignment_ids=request.source_assignment_ids,
        student_ids=student_ids,
    )
    all_false_ids = {
        knowledge_point_id
        for values in false_by_student.values()
        for knowledge_point_id in values
    }
    candidates = _candidates(db, all_false_ids)
    total_requested = QUESTIONS_PER_KNOWLEDGE_POINT * len(all_false_ids)
    params = _recommendation_parameters(db, all_false_ids, total_requested)
    source_ids_by_student = {student_id: set() for student_id in student_ids}
    for source_assignment_id in request.source_assignment_ids:
        values = history_source_question_ids(
            db,
            source_assignment_id=source_assignment_id,
            student_ids=student_ids,
        )
        for student_id in student_ids:
            source_ids_by_student[student_id].update(values[student_id])

    outputs = []
    for student in students:
        false_points = false_by_student[student.id]
        used = set(source_ids_by_student[student.id])
        selected = []
        point_outputs = []
        for knowledge_point_id, value in sorted(
            false_points.items(), key=lambda item: item[1]["code"]
        ):
            point_outputs.append(WeeklySummaryKnowledgePointOut(
                knowledge_point_code=value["code"],
                knowledge_point_name=value["name"],
                evidence=value["evidence"],
            ))
            chosen = [
                candidate for candidate in candidates.get(knowledge_point_id, [])
                if candidate.question_id not in used
            ][:QUESTIONS_PER_KNOWLEDGE_POINT]
            for candidate in chosen:
                used.add(candidate.question_id)
                assert params is not None
                item = score_candidate(candidate, None, params, "review", at)
                selected.append(item.model_copy(update={
                    "source_module": "weekly_summary",
                    "target_difficulty": FIXED_DIFFICULTY,
                    "reason_codes": [
                        "weekly_summary", "knowledge_status_no", "fixed_difficulty_3",
                    ],
                    "reason_text": (
                        f"周总结：知识点“{value['name']}”的历史回顾再次答错，"
                        "当前暂按 3 星安排两道题。"
                    ),
                    "evidence": value["evidence"],
                }))
        requested = QUESTIONS_PER_KNOWLEDGE_POINT * len(false_points)
        shortfall = requested - len(selected)
        outputs.append(WeeklySummaryStudentPreview(
            student_id=student.id,
            student_code=student.student_code,
            student_name=student.display_name,
            false_knowledge_points=point_outputs,
            requested_count=requested,
            selected_count=len(selected),
            shortfall=shortfall,
            status=(
                "no_review_needed" if requested == 0 else
                "ready" if shortfall == 0 else "insufficient_candidates"
            ),
            items=selected,
        ))
    paper_students = [item for item in outputs if item.requested_count > 0]
    involved_codes = sorted({
        point.knowledge_point_code
        for item in outputs for point in item.false_knowledge_points
    })
    return WeeklySummaryPreviewOut(
        as_of=at,
        student_count=len(outputs),
        paper_student_count=len(paper_students),
        ready_count=sum(item.shortfall == 0 for item in paper_students),
        shortfall_student_count=sum(item.shortfall > 0 for item in paper_students),
        source_assignment_ids=request.source_assignment_ids,
        involved_knowledge_point_codes=involved_codes,
        students=outputs,
    )


def _fingerprint(request: WeeklySummaryCreateRequest) -> str:
    payload = request.model_dump(mode="json", exclude={"idempotency_key"})
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def create_weekly_summary(
    db: Session, request: WeeklySummaryCreateRequest, *, as_of: datetime | None = None,
) -> tuple[Assignment, bool]:
    fingerprint = _fingerprint(request)
    existing = db.scalar(select(Assignment).where(
        Assignment.idempotency_key == request.idempotency_key
    ))
    if existing is not None:
        if (
            existing.assignment_type != "weekly_summary"
            or (existing.generation_config or {}).get("request_fingerprint") != fingerprint
        ):
            raise RuntimeError("idempotency key was already used for another request")
        return existing, False

    plan = WeeklySummaryPlanRequest(
        class_id=request.class_id,
        source_assignment_ids=request.source_assignment_ids,
    )
    preview = preview_weekly_summary(db, plan, as_of=as_of)
    false_codes = preview.involved_knowledge_point_codes
    points = list(db.scalars(select(KnowledgePoint).where(
        KnowledgePoint.code.in_(false_codes)
    ))) if false_codes else []
    params = _recommendation_parameters(
        db, {item.id for item in points},
        QUESTIONS_PER_KNOWLEDGE_POINT * len(points),
    )
    assignment = Assignment(
        idempotency_key=request.idempotency_key,
        title=request.title,
        class_id=request.class_id,
        chapter_code="weekly-summary",
        teaching_scope={
            "source_assignment_ids": [str(value) for value in request.source_assignment_ids],
            "knowledge_point_codes": false_codes,
            "fixed_difficulty": FIXED_DIFFICULTY,
            "questions_per_knowledge_point": QUESTIONS_PER_KNOWLEDGE_POINT,
        },
        generation_config={
            "schema_version": RULE_VERSION,
            "request_fingerprint": fingerprint,
            "as_of": preview.as_of.isoformat(),
            "source_assignment_ids": [str(value) for value in request.source_assignment_ids],
            "recommendation_parameters": params.model_dump(mode="json") if params else None,
            "allow_shortfall_draft": True,
        },
        assignment_type="weekly_summary",
    )
    db.add(assignment)
    db.flush()
    questions = load_questions(db, {
        item.question_id for student in preview.students for item in student.items
    })
    for student in preview.students:
        if student.requested_count == 0:
            continue
        paper = PaperInstance(
            assignment_id=assignment.id,
            student_id=student.student_id,
            version_no=1,
            status="draft",
            recommendation_audit={
                "schema_version": RULE_VERSION,
                "student_id": str(student.student_id),
                "as_of": preview.as_of.isoformat(),
                "status": student.status,
                "requested_count": student.requested_count,
                "selected_count": student.selected_count,
                "shortfall": student.shortfall,
                "new_shortfall": 0,
                "review_shortfall": student.shortfall,
                "false_knowledge_points": [
                    item.model_dump(mode="json")
                    for item in student.false_knowledge_points
                ],
                "items": [item.model_dump(mode="json") for item in student.items],
            },
            edit_history=[],
            edit_revision=1,
        )
        db.add(paper)
        db.flush()
        for order_no, selected in enumerate(student.items, start=1):
            question = questions[selected.question_id]
            db.add(new_item(
                paper_id=paper.id,
                order_no=order_no,
                payload=snapshot_payload(question),
                selection_audit={
                    "schema_version": "weekly-summary-selection-v1",
                    "source": "weekly_summary",
                    "source_module": "weekly_summary",
                    "selected_at": preview.as_of.isoformat(),
                    **selected.model_dump(mode="json"),
                },
            ))
    db.flush()
    return assignment, True
