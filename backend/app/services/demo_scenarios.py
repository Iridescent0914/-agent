from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Assignment, Attempt, PaperInstance, PaperInstanceItem, Student
from app.schemas.assignments import AssignmentCreateRequest
from app.schemas.submissions import AttemptReviewRequest, ManualAnswerIn, ManualSubmissionRequest
from app.services.class_analytics import build_assignment_overview, build_class_overview
from app.services.paper_generation import create_assignment
from app.services.paper_workflow import publish_paper
from app.services.submission_workflow import create_manual_submission, review_attempt


class DemoScenarioError(RuntimeError):
    pass


def _students(db: Session, codes: list[str], class_name: str) -> list[Student]:
    students = list(
        db.scalars(
            select(Student)
            .where(Student.student_code.in_(codes), Student.archived_at.is_(None))
            .order_by(Student.student_code)
        )
    )
    found = {student.student_code for student in students}
    if found != set(codes):
        raise DemoScenarioError(f"missing demo students: {sorted(set(codes) - found)}")
    if any(not student.is_demo or student.class_name != class_name for student in students):
        raise DemoScenarioError("scenario students must be active demo students in the configured class")
    return students


def _answers(items: list[PaperInstanceItem], *, wrong_count: int = 0) -> list[ManualAnswerIn]:
    return [
        ManualAnswerIn(
            paper_instance_item_id=item.id,
            order_no=item.order_no,
            response_status="blank" if index < wrong_count else "answered",
            answer=None if index < wrong_count else item.answer_snapshot,
        )
        for index, item in enumerate(items)
    ]


def _prepare_submission(
    db: Session,
    *,
    paper: PaperInstance,
    student: Student,
    state: str,
    answered_at: datetime,
) -> None:
    if state == "unsubmitted":
        return
    items = list(
        db.scalars(
            select(PaperInstanceItem)
            .where(PaperInstanceItem.paper_instance_id == paper.id)
            .order_by(PaperInstanceItem.order_no)
        )
    )
    wrong_count = 2 if state == "mixed_completed" else 1 if state == "corrected_completed" else 0
    detail = create_manual_submission(
        db,
        paper.id,
        ManualSubmissionRequest(
            idempotency_key=f"m5-demo-submit-{student.student_code.lower()}-{paper.version_no}",
            student_id=student.id,
            answered_at=answered_at,
            answers=_answers(items, wrong_count=wrong_count),
        ),
    )
    attempts = [item.attempt for item in detail.items if item.attempt is not None]
    if not attempts:
        raise DemoScenarioError(f"submission produced no attempts for {student.student_code}")
    if state == "needs_review":
        target = attempts[0]
        review_attempt(
            db,
            target.id,
            AttemptReviewRequest(
                action="reopen",
                expected_revision_no=target.revision_no,
                reason="M5 固定演示：保留一题待教师复核",
                actor="m5_demo_teacher",
            ),
        )
    elif state == "corrected_completed":
        target = attempts[0]
        review_attempt(
            db,
            target.id,
            AttemptReviewRequest(
                action="confirm",
                expected_revision_no=target.revision_no,
                is_correct=True,
                reason="M5 固定演示：教师核对后由错改对",
                actor="m5_demo_teacher",
            ),
        )


def _summary(db: Session, assignment: Assignment, *, class_name: str, chapter_code: str) -> dict[str, Any]:
    assignment_result = build_assignment_overview(db, assignment.id)
    class_result = build_class_overview(
        db, class_name=class_name, chapter_code=chapter_code
    )
    return {
        "assignment_id": str(assignment.id),
        "assignment_seed_key": assignment.seed_key,
        "assignment": assignment_result.model_dump(mode="json"),
        "class_summary": class_result.summary.model_dump(mode="json"),
    }


def prepare_m5_scenario(
    db: Session,
    config: dict[str, Any],
    scenario_id: str,
    *,
    check_only: bool = False,
) -> tuple[dict[str, Any], bool]:
    scenarios = config.get("scenarios", {})
    scenario = scenarios.get(scenario_id)
    if not isinstance(scenario, dict):
        raise DemoScenarioError(f"unknown scenario: {scenario_id}")
    class_name = config["class_name"]
    chapter_code = config["chapter_code"]
    students = _students(db, config["student_codes"], class_name)
    existing = db.scalar(
        select(Assignment).where(Assignment.seed_key == scenario["assignment_seed_key"])
    )
    if existing is not None:
        return _summary(
            db, existing, class_name=class_name, chapter_code=chapter_code
        ), False
    if check_only:
        raise DemoScenarioError(f"scenario is not prepared: {scenario_id}")

    as_of = datetime.fromisoformat(config["fixed_as_of"])
    request = AssignmentCreateRequest(
        idempotency_key=scenario["idempotency_key"],
        title=scenario["assignment_title"],
        student_ids=[student.id for student in students],
        recommendation_parameters=config["recommendation_parameters"],
    )
    assignment, created = create_assignment(db, request, as_of=as_of)
    if not created:
        raise DemoScenarioError("scenario idempotency key exists without its seed key")
    assignment.seed_key = scenario["assignment_seed_key"]
    assignment.created_at = as_of - timedelta(days=1)
    db.flush()

    by_id = {student.id: student for student in students}
    papers = list(
        db.scalars(
            select(PaperInstance)
            .where(PaperInstance.assignment_id == assignment.id)
            .order_by(PaperInstance.student_id)
        )
    )
    if len(papers) != len(students):
        raise DemoScenarioError("scenario did not generate exactly one paper per student")
    for index, paper in enumerate(papers):
        student = by_id[paper.student_id]
        state = scenario["student_states"].get(student.student_code)
        if state is None:
            raise DemoScenarioError(f"missing state for {student.student_code}")
        publish_paper(
            db,
            paper.id,
            expected_revision=paper.edit_revision,
            acknowledge_shortfall=True,
            acknowledgement_reason="M5 固定演示允许记录候选不足",
        )
        _prepare_submission(
            db,
            paper=paper,
            student=student,
            state=state,
            answered_at=as_of + timedelta(minutes=index),
        )
    db.flush()
    return _summary(
        db, assignment, class_name=class_name, chapter_code=chapter_code
    ), True


def validate_m5_scenario(
    db: Session, config: dict[str, Any], scenario_id: str
) -> list[str]:
    errors: list[str] = []
    scenario = config.get("scenarios", {}).get(scenario_id)
    if not isinstance(scenario, dict):
        return [f"unknown scenario: {scenario_id}"]
    assignment = db.scalar(
        select(Assignment).where(Assignment.seed_key == scenario["assignment_seed_key"])
    )
    if assignment is None:
        return [f"scenario is not prepared: {scenario_id}"]
    result = build_assignment_overview(db, assignment.id)
    actual = {item.student_code: item.grading_status for item in result.students}
    expected = {
        code: "completed" if state in {"mixed_completed", "completed", "corrected_completed"} else state
        for code, state in scenario["student_states"].items()
    }
    if actual != expected:
        errors.append(f"student state mismatch: expected={expected}, actual={actual}")
    if scenario_id == "m5_showcase":
        corrected_student = next(student for student in _students(db, ["S005"], config["class_name"]))
        revised = list(
            db.scalars(
                select(Attempt)
                .join(
                    PaperInstanceItem,
                    PaperInstanceItem.id == Attempt.paper_instance_item_id,
                )
                .join(
                    PaperInstance,
                    PaperInstance.id == PaperInstanceItem.paper_instance_id,
                )
                .where(
                    PaperInstance.assignment_id == assignment.id,
                    Attempt.student_id == corrected_student.id,
                    Attempt.revision_no > 1,
                    Attempt.manual_override.is_(True),
                )
            )
        )
        if not revised:
            errors.append("S005 has no corrected attempt revision")
    return errors

