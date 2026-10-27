from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
import hashlib
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models import (
    Assignment, PaperInstance, PaperInstanceItem, Question, SchoolClass, Student,
)
from app.schemas.submissions import (
    AttemptReviewRequest, ManualAnswerIn, ManualSubmissionRequest,
)
from app.services.paper_snapshots import new_item, snapshot_payload
from app.services.paper_workflow import paper_items, publish_paper
from app.services.recommendations import (
    assignment_is_fully_graded, history_wrong_answers,
)
from app.services.submission_workflow import create_manual_submission, review_attempt


DEFAULT_CLASS_NAME = "六年级 Demo 班"
DEFAULT_SEED_PREFIX = "HISTORY-REVIEW-DEMO"
DEFAULT_AS_OF = datetime(2026, 10, 27, 8, 0, tzinfo=timezone.utc)


class HistoryDemoError(RuntimeError):
    pass


def _class_students(db: Session, class_name: str) -> tuple[SchoolClass, list[Student]]:
    school_class = db.scalar(select(SchoolClass).where(SchoolClass.name == class_name))
    if school_class is None:
        raise HistoryDemoError(f"班级不存在：{class_name}")
    students = list(db.scalars(
        select(Student).where(
            Student.class_id == school_class.id,
            Student.archived_at.is_(None),
        ).order_by(Student.student_code)
    ))
    if not students:
        raise HistoryDemoError(f"班级没有在读学生：{class_name}")
    return school_class, students


def _question_groups(db: Session) -> list[list[Question]]:
    questions = list(db.scalars(
        select(Question)
        .options(selectinload(Question.main_knowledge_point))
        .where(
            Question.chapter_code == "math.g6.rational",
            Question.is_required.is_(True),
            Question.archived_at.is_(None),
            Question.grading_readiness == "machine_ready",
            Question.source_review_status == "reviewed",
            Question.difficulty <= 3,
        )
        .order_by(Question.original_qid, Question.id)
    ).unique())
    grouped: dict[tuple[str, int], list[Question]] = defaultdict(list)
    for question in questions:
        grouped[(str(question.main_knowledge_point_id), question.difficulty)].append(question)
    groups = [items for items in grouped.values() if len(items) >= 2]
    groups.sort(key=lambda items: (items[0].main_knowledge_point.code, items[0].difficulty))
    if not groups:
        raise HistoryDemoError("没有可用于历史回顾演示的同知识点、同难度题组")
    return groups


def _answers(
    items: list[PaperInstanceItem], *, wrong_count: int,
) -> list[ManualAnswerIn]:
    return [
        ManualAnswerIn(
            paper_instance_item_id=item.id,
            order_no=item.order_no,
            response_status="blank" if index < wrong_count else "answered",
            answer=None if index < wrong_count else _student_answer(item.answer_snapshot),
        )
        for index, item in enumerate(items)
    ]


def _student_answer(answer_snapshot: Any) -> Any:
    """Convert a structured answer key into the manual-answer input shape."""

    if isinstance(answer_snapshot, str):
        return answer_snapshot
    if not isinstance(answer_snapshot, dict) or answer_snapshot.get("kind") != "group":
        raise HistoryDemoError("演示题标准答案不能转换为学生作答")
    items = answer_snapshot.get("items")
    if not isinstance(items, list):
        raise HistoryDemoError("演示题组合答案缺少小题列表")
    converted = []
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("item_id"), str):
            raise HistoryDemoError("演示题组合答案的小题结构无效")
        value = item.get("answer")
        if value is not None and not isinstance(value, str):
            value = item.get("reference_answer_raw")
        if value is not None and not isinstance(value, str):
            raise HistoryDemoError("演示题组合答案不能转换为文本")
        converted.append({"item_id": item["item_id"], "answer": value})
    return {"kind": "group", "items": converted}


def _existing_summary(
    db: Session, assignment: Assignment, students: list[Student],
) -> dict[str, Any]:
    if not assignment_is_fully_graded(db, assignment.id):
        raise HistoryDemoError(f"既有演示作业未全部批改：{assignment.title}")
    errors = history_wrong_answers(
        db,
        source_assignment_id=assignment.id,
        class_id=assignment.class_id,
        student_ids=[student.id for student in students],
    )
    by_id = {student.id: student.student_code for student in students}
    return {
        "assignment_id": str(assignment.id),
        "title": assignment.title,
        "created_at": assignment.created_at.astimezone(timezone.utc).isoformat(),
        "wrong_counts": {
            by_id[student_id]: len(items) for student_id, items in errors.items()
        },
    }


def prepare_history_demo(
    db: Session,
    *,
    class_name: str = DEFAULT_CLASS_NAME,
    seed_prefix: str = DEFAULT_SEED_PREFIX,
    scenario_count: int = 2,
    question_count: int = 4,
    as_of: datetime = DEFAULT_AS_OF,
) -> tuple[list[dict[str, Any]], int]:
    """Create completed, idempotent source assignments for history review."""

    if not 1 <= scenario_count <= 5:
        raise ValueError("scenario_count must be between 1 and 5")
    if not 1 <= question_count <= 10:
        raise ValueError("question_count must be between 1 and 10")
    school_class, students = _class_students(db, class_name)
    groups = _question_groups(db)
    selected_groups = groups[:question_count]
    if len(selected_groups) < question_count:
        raise HistoryDemoError(
            f"只有 {len(selected_groups)} 组题具备历史替换库存，少于请求的 {question_count} 组"
        )

    summaries: list[dict[str, Any]] = []
    created_count = 0
    for scenario_index in range(scenario_count):
        seed_key = f"{seed_prefix}-{scenario_index + 1}"
        existing = db.scalar(select(Assignment).where(Assignment.seed_key == seed_key))
        if existing is not None:
            if existing.class_id != school_class.id:
                raise HistoryDemoError(f"演示种子键已被其他班级使用：{seed_key}")
            summaries.append(_existing_summary(db, existing, students))
            continue

        created_at = as_of - timedelta(days=scenario_count - scenario_index)
        questions = [
            group[scenario_index % len(group)] for group in selected_groups
        ]
        assignment = Assignment(
            seed_key=seed_key,
            title=f"历史回顾测试：已批改必做题 {scenario_index + 1}",
            class_id=school_class.id,
            chapter_code="math.g6.rational",
            teaching_scope={
                "source": "history_review_demo",
                "knowledge_point_codes": [
                    question.main_knowledge_point.code for question in questions
                ],
            },
            generation_config={
                "source": "history_review_demo",
                "rule": "completed_required_questions_with_mixed_results",
            },
            assignment_type="practice",
            created_at=created_at,
        )
        db.add(assignment)
        db.flush()

        for student_index, student in enumerate(students):
            paper = PaperInstance(
                assignment_id=assignment.id,
                student_id=student.id,
                version_no=1,
                status="draft",
                recommendation_audit={
                    "schema_version": "history-review-demo-v1",
                    "requested_count": len(questions),
                    "selected_count": len(questions),
                    "shortfall": 0,
                },
                edit_history=[],
                edit_revision=1,
                created_at=created_at,
            )
            db.add(paper)
            db.flush()
            for order_no, question in enumerate(questions, start=1):
                db.add(new_item(
                    paper_id=paper.id,
                    order_no=order_no,
                    payload=snapshot_payload(question),
                    selection_audit={
                        "schema_version": "m3-selection-audit-v1",
                        "source": "recommendation",
                        "source_module": "required",
                        "reason_codes": [
                            "required_question", "history_review_demo",
                            f"difficulty_{question.difficulty}",
                        ],
                        "knowledge_point_code": question.main_knowledge_point.code,
                        "knowledge_point_name": question.main_knowledge_point.name,
                    },
                ))
            db.flush()
            publish_paper(db, paper.id, expected_revision=paper.edit_revision)
            items = paper_items(db, paper.id)
            # Every scenario contains both an all-correct student and students
            # with one or two eligible wrong mandatory questions.
            wrong_count = (student_index + scenario_index) % 3
            submission_key = "history-demo-" + hashlib.sha256(
                f"{seed_key}:{student.id}".encode("utf-8")
            ).hexdigest()[:32]
            detail = create_manual_submission(
                db,
                paper.id,
                ManualSubmissionRequest(
                    idempotency_key=(
                        submission_key
                    ),
                    student_id=student.id,
                    answered_at=created_at + timedelta(hours=2, minutes=student_index),
                    answers=_answers(items, wrong_count=wrong_count),
                ),
            )
            for result_item in detail.items:
                attempt = result_item.attempt
                if attempt is None or attempt.grade_status != "needs_review":
                    continue
                review_attempt(
                    db,
                    attempt.id,
                    AttemptReviewRequest(
                        action="confirm",
                        expected_revision_no=attempt.revision_no,
                        is_correct=result_item.order_no > wrong_count,
                        reason="历史回顾演示数据：教师确认组合答案",
                        actor="history_demo_teacher",
                    ),
                )

        db.flush()
        if not assignment_is_fully_graded(db, assignment.id):
            raise HistoryDemoError(f"演示作业没有形成全部批改状态：{assignment.title}")
        summaries.append(_existing_summary(db, assignment, students))
        created_count += 1

    return summaries, created_count
