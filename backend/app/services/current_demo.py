from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    Assignment, KnowledgePoint, PaperInstance, Question, SchoolClass,
    Student, StudentKnowledgeStatus, Submission,
)
from app.schemas.assignments import (
    AssignmentCreateRequest, AssignmentFilterConfig, HistoryQuestionFilter,
    PersonalizedQuestionFilter, RequiredQuestionFilter,
)
from app.schemas.recommendations import RecommendationRequest
from app.schemas.submissions import (
    AttemptReviewRequest, ManualAnswerIn, ManualSubmissionRequest,
)
from app.schemas.weekly_summary import WeeklySummaryCreateRequest
from app.services.history_demo import prepare_history_demo
from app.services.paper_generation import create_assignment
from app.services.paper_workflow import paper_items, publish_paper
from app.services.recommendations import assignment_is_fully_graded
from app.services.submission_workflow import create_manual_submission, review_attempt
from app.services.weekly_summary import create_weekly_summary


RULE_VERSION = "current-demo-v1"


class CurrentDemoError(RuntimeError):
    pass


def load_demo_config(path: Path) -> dict[str, Any]:
    config = json.loads(path.read_text(encoding="utf-8"))
    if config.get("schema_version") != RULE_VERSION:
        raise CurrentDemoError("unsupported current demo schema version")
    if len(config.get("students") or []) != 5:
        raise CurrentDemoError("current demo must define exactly five students")
    student_codes = [item.get("student_code") for item in config["students"]]
    if None in student_codes or len(set(student_codes)) != 5:
        raise CurrentDemoError("current demo student codes must be present and unique")
    configured_codes = set(student_codes)
    for section, key in (
        ("history_assignment", "wrong_student_codes"),
        ("weekly_summary", "weak_student_codes"),
        ("weekly_summary", "leave_draft_student_codes"),
    ):
        unknown = set(config[section].get(key) or []) - configured_codes
        if unknown:
            raise CurrentDemoError(
                f"{section}.{key} contains unknown students: {sorted(unknown)}"
            )
    return config


def _ensure_class(db: Session, config: dict[str, Any]) -> tuple[SchoolClass, bool]:
    values = config["class"]
    school_class = db.scalar(select(SchoolClass).where(
        SchoolClass.name == values["name"]
    ))
    if school_class is None:
        school_class = SchoolClass(**values)
        db.add(school_class)
        db.flush()
        return school_class, True
    actual = (school_class.grade, school_class.school_year, school_class.teacher_name)
    expected = (values["grade"], values["school_year"], values.get("teacher_name"))
    if actual != expected:
        raise CurrentDemoError(
            "同名班级的年级、学年或教师不符合当前演示合同，请先清理旧业务数据"
        )
    return school_class, False


def _ensure_students(
    db: Session, school_class: SchoolClass, config: dict[str, Any],
) -> tuple[list[Student], int]:
    students = []
    created = 0
    for values in config["students"]:
        student = db.scalar(select(Student).where(
            Student.student_code == values["student_code"]
        ))
        if student is None:
            student = Student(
                **values,
                class_id=school_class.id,
                class_name=school_class.name,
                is_demo=True,
            )
            db.add(student)
            db.flush()
            created += 1
        elif (
            student.display_name != values["display_name"]
            or student.class_id != school_class.id
            or student.class_name != school_class.name
            or not student.is_demo
            or student.archived_at is not None
        ):
            raise CurrentDemoError(
                f"学生编号 {student.student_code} 已被不兼容的数据占用，请先清理旧业务数据"
            )
        students.append(student)
    active_codes = set(db.scalars(select(Student.student_code).where(
        Student.class_id == school_class.id,
        Student.archived_at.is_(None),
    )))
    expected_codes = {item["student_code"] for item in config["students"]}
    if active_codes != expected_codes:
        raise CurrentDemoError(
            "演示班已有合同外的在读学生，请先清理旧业务数据"
        )
    return students, created


def _expr_scope(db: Session) -> tuple[Question, KnowledgePoint]:
    rows = db.execute(
        select(Question, KnowledgePoint)
        .join(KnowledgePoint, KnowledgePoint.id == Question.main_knowledge_point_id)
        .where(
            Question.chapter_code == "math.g6.expr",
            Question.grade == "六年级上册",
            Question.is_required.is_(True),
            Question.archived_at.is_(None),
            Question.grading_readiness == "machine_ready",
            Question.source_review_status == "reviewed",
        )
        .order_by(Question.original_qid, Question.id)
    ).all()
    for question, knowledge_point in rows:
        if knowledge_point.core_competencies:
            return question, knowledge_point
    raise CurrentDemoError("第二章没有可用于当前演示的必做题与能力映射")


def _history_request(
    db: Session, school_class: SchoolClass, source: Assignment,
    config: dict[str, Any],
) -> AssignmentCreateRequest:
    question, knowledge_point = _expr_scope(db)
    competency = knowledge_point.core_competencies[0]
    params = RecommendationRequest(
        chapter_code=question.chapter_code,
        chapter_codes=[question.chapter_code],
        grade_codes=[question.grade],
        new_knowledge_point_codes=[knowledge_point.code],
        review_knowledge_point_codes=[],
        new_count=1,
        review_count=0,
        question_types=["choice", "fill_blank", "true_false"],
        base_difficulty=question.difficulty,
        min_difficulty=1,
        max_difficulty=5,
        avoid_recent_days=0,
        max_knowledge_point_ratio=1,
    )
    return AssignmentCreateRequest(
        class_id=school_class.id,
        student_scope="all_class",
        student_ids=[],
        recommendation_parameters=params,
        filter_config=AssignmentFilterConfig(
            grade_codes=[question.grade],
            chapter_codes=[question.chapter_code],
            required=RequiredQuestionFilter(
                knowledge_point_codes=[knowledge_point.code],
                core_literacies=[],
                difficulty_counts={str(question.difficulty): 1},
            ),
            personalized=PersonalizedQuestionFilter(
                knowledge_point_codes=[knowledge_point.code],
                primary_competency=competency,
                question_count=3,
            ),
            history=HistoryQuestionFilter(assignment_id=source.id),
        ),
        idempotency_key=config["history_assignment"]["idempotency_key"],
        title=config["history_assignment"]["title"],
    )


def _manual_value(answer_snapshot: Any) -> Any:
    if isinstance(answer_snapshot, str):
        return answer_snapshot
    if not isinstance(answer_snapshot, dict) or answer_snapshot.get("kind") != "group":
        raise CurrentDemoError("演示题标准答案不能转换为手工作答")
    converted = []
    for item in answer_snapshot.get("items") or []:
        value = item.get("answer")
        if value is not None and not isinstance(value, str):
            value = item.get("reference_answer_raw")
        converted.append({"item_id": item["item_id"], "answer": value})
    return {"kind": "group", "items": converted}


def _publish(db: Session, paper: PaperInstance) -> None:
    if paper.status == "published":
        return
    items = paper_items(db, paper.id)
    requested = int((paper.recommendation_audit or {}).get("requested_count", len(items)))
    publish_paper(
        db,
        paper.id,
        expected_revision=paper.edit_revision,
        acknowledge_shortfall=len(items) < requested,
        acknowledgement_reason=(
            "当前题库库存不足，演示按阶段二规则保留可用题目"
            if len(items) < requested else None
        ),
    )


def _submit(
    db: Session, paper: PaperInstance, *, wrong_modules: set[str],
    wrong_first_only: bool, answered_at: datetime,
) -> bool:
    existing = db.scalar(select(Submission).where(
        Submission.paper_instance_id == paper.id
    ))
    if existing is not None:
        return False
    items = paper_items(db, paper.id)
    answers = []
    expected: dict[Any, bool] = {}
    wrong_used = False
    for item in items:
        module = (item.selection_audit or {}).get("source_module")
        should_be_wrong = module in wrong_modules and (
            not wrong_first_only or not wrong_used
        )
        wrong_used = wrong_used or should_be_wrong
        expected[item.id] = not should_be_wrong
        answers.append(ManualAnswerIn(
            paper_instance_item_id=item.id,
            order_no=item.order_no,
            response_status="blank" if should_be_wrong else "answered",
            answer=None if should_be_wrong else _manual_value(item.answer_snapshot),
        ))
    submission_key = "current-demo-" + hashlib.sha256(
        str(paper.id).encode("utf-8")
    ).hexdigest()[:32]
    detail = create_manual_submission(
        db,
        paper.id,
        ManualSubmissionRequest(
            idempotency_key=submission_key,
            student_id=paper.student_id,
            answered_at=answered_at,
            answers=answers,
        ),
    )
    for result in detail.items:
        attempt = result.attempt
        if attempt is None:
            raise CurrentDemoError("演示提交没有生成作答记录")
        intended = expected[result.id]
        if attempt.grade_status == "needs_review":
            review_attempt(
                db,
                attempt.id,
                AttemptReviewRequest(
                    action="confirm",
                    expected_revision_no=attempt.revision_no,
                    is_correct=intended,
                    reason="当前版本演示数据：教师确认作答结果",
                    actor="current_demo_teacher",
                ),
            )
        elif attempt.is_correct is not intended:
            raise CurrentDemoError("确定性判分结果与演示合同不一致")
    return True


def _papers(db: Session, assignment_id: Any) -> list[tuple[PaperInstance, Student]]:
    return list(db.execute(
        select(PaperInstance, Student)
        .join(Student, Student.id == PaperInstance.student_id)
        .where(PaperInstance.assignment_id == assignment_id)
        .order_by(Student.student_code)
    ).all())


def prepare_current_demo(
    db: Session, config: dict[str, Any],
) -> dict[str, Any]:
    school_class, class_created = _ensure_class(db, config)
    students, students_created = _ensure_students(db, school_class, config)

    source_config = config["required_sources"]
    source_summaries, source_created = prepare_history_demo(
        db,
        class_name=school_class.name,
        seed_prefix=source_config["seed_prefix"],
        title_prefix=source_config["title_prefix"],
        scenario_count=source_config["scenario_count"],
        question_count=source_config["question_count"],
    )
    source_key = f"{source_config['seed_prefix']}-{source_config['scenario_count']}"
    source_assignment = db.scalar(select(Assignment).where(
        Assignment.seed_key == source_key
    ))
    if source_assignment is None:
        raise CurrentDemoError("历史回顾来源作业创建失败")

    history_request = _history_request(db, school_class, source_assignment, config)
    history_assignment, history_created = create_assignment(db, history_request)
    history_seed_key = config["history_assignment"]["seed_key"]
    if history_assignment.seed_key not in (None, history_seed_key):
        raise CurrentDemoError("历史回顾演示种子键冲突")
    history_assignment.seed_key = history_seed_key
    db.flush()

    wrong_history = set(config["history_assignment"]["wrong_student_codes"])
    history_submissions_created = 0
    history_answered_at = datetime.now(timezone.utc).replace(microsecond=0)
    for index, (paper, student) in enumerate(_papers(db, history_assignment.id)):
        _publish(db, paper)
        history_submissions_created += int(_submit(
            db,
            paper,
            wrong_modules={"history"} if student.student_code in wrong_history else set(),
            wrong_first_only=False,
            answered_at=history_answered_at,
        ))
    if not assignment_is_fully_graded(db, history_assignment.id):
        raise CurrentDemoError("历史回顾演示作业没有全部批改完成")

    weekly_config = config["weekly_summary"]
    weekly_assignment, weekly_created = create_weekly_summary(
        db,
        WeeklySummaryCreateRequest(
            class_id=school_class.id,
            source_assignment_ids=[history_assignment.id],
            title=weekly_config["title"],
            idempotency_key=weekly_config["idempotency_key"],
        ),
    )
    weekly_seed_key = weekly_config["seed_key"]
    if weekly_assignment.seed_key not in (None, weekly_seed_key):
        raise CurrentDemoError("周总结演示种子键冲突")
    weekly_assignment.seed_key = weekly_seed_key
    db.flush()

    weak_students = set(weekly_config["weak_student_codes"])
    leave_draft = set(weekly_config["leave_draft_student_codes"])
    weekly_submissions_created = 0
    for paper, student in _papers(db, weekly_assignment.id):
        if student.student_code in leave_draft:
            if paper.status != "draft":
                raise CurrentDemoError("应保留草稿的周总结试卷已经发布")
            continue
        if not paper_items(db, paper.id):
            continue
        _publish(db, paper)
        weekly_submissions_created += int(_submit(
            db,
            paper,
            wrong_modules={"weekly_summary"} if student.student_code in weak_students else set(),
            wrong_first_only=True,
            answered_at=history_answered_at + timedelta(hours=1),
        ))

    status_rows = db.execute(
        select(Student.student_code, StudentKnowledgeStatus.status, func.count())
        .join(Student, Student.id == StudentKnowledgeStatus.student_id)
        .where(Student.class_id == school_class.id)
        .group_by(Student.student_code, StudentKnowledgeStatus.status)
        .order_by(Student.student_code, StudentKnowledgeStatus.status)
    ).all()
    return {
        "schema_version": RULE_VERSION,
        "class_id": str(school_class.id),
        "class_name": school_class.name,
        "class_created": class_created,
        "student_count": len(students),
        "students_created": students_created,
        "source_assignments_created": source_created,
        "source_assignments": source_summaries,
        "history_assignment_id": str(history_assignment.id),
        "history_assignment_created": history_created,
        "history_submissions_created": history_submissions_created,
        "weekly_assignment_id": str(weekly_assignment.id),
        "weekly_assignment_created": weekly_created,
        "weekly_submissions_created": weekly_submissions_created,
        "knowledge_status_counts": [
            {"student_code": code, "status": status, "count": int(count)}
            for code, status, count in status_rows
        ],
    }
