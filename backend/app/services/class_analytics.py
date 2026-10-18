from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Assignment, Attempt, PaperInstance, PaperInstanceItem, Student, Submission
from app.schemas.analytics import (
    AssignmentAnalyticsOut,
    AssignmentProgressStudent,
    AssignmentProgressSummary,
    ClassAnalyticsSummary,
    ClassKnowledgePointAnalytics,
    ClassFocusPoint,
    ClassOption,
    ClassOverviewOut,
    KnowledgePointStudentAnalytics,
    KnowledgePointStudentListOut,
)
from app.services.mastery import RULE_VERSION
from app.services.student_profile import (
    ProfileContext,
    knowledge_point_catalog,
    load_context,
    mastery_items,
)


class AnalyticsDataError(RuntimeError):
    """Raised when persisted workflow state cannot be aggregated unambiguously."""


def _ratio(numerator: int, denominator: int) -> float | None:
    if denominator == 0:
        return None
    return float(
        (Decimal(numerator) / Decimal(denominator)).quantize(
            Decimal("0.0001"), rounding=ROUND_HALF_UP
        )
    )


def _mean(values: list[float]) -> float | None:
    if not values:
        return None
    return float(
        (sum(Decimal(str(value)) for value in values) / Decimal(len(values))).quantize(
            Decimal("0.0001"), rounding=ROUND_HALF_UP
        )
    )


def _active_students(db: Session, class_name: str) -> list[Student]:
    return list(
        db.scalars(
            select(Student)
            .where(Student.class_name == class_name, Student.archived_at.is_(None))
            .order_by(Student.student_code, Student.id)
        )
    )


def _record_knowledge_code(record: object) -> str | None:
    main = getattr(record, "main_knowledge_point", None)
    if not isinstance(main, dict):
        return None
    code = main.get("id")
    return code if isinstance(code, str) else None


def list_classes(db: Session) -> list[ClassOption]:
    rows = db.execute(
        select(Student.class_name, func.count(Student.id))
        .where(Student.archived_at.is_(None))
        .group_by(Student.class_name)
        .order_by(Student.class_name)
    ).all()
    return [ClassOption(class_name=name, student_count=count) for name, count in rows]


def _contexts(db: Session, students: list[Student]) -> list[ProfileContext]:
    # The demo has five students. Reusing load_context keeps class and individual
    # evidence semantics identical. A future batch loader should sit underneath
    # load_context instead of creating a second definition of valid evidence.
    return [load_context(db, student.id) for student in students]


def build_class_overview(
    db: Session, *, class_name: str, chapter_code: str
) -> ClassOverviewOut:
    students = _active_students(db, class_name)
    if not students:
        raise LookupError("class not found")
    catalog = knowledge_point_catalog(db, chapter_code)
    if not catalog:
        raise LookupError("chapter not found")
    catalog_codes = {item.code for item in catalog}
    contexts = _contexts(db, students)
    all_catalog_codes = {item.code for item in contexts[0].catalog}
    mastery_by_student = {
        context.student.id: {
            item.knowledge_point_code: item
            for item in mastery_items(context, chapter_code)
        }
        for context in contexts
    }

    knowledge_rows: list[ClassKnowledgePointAnalytics] = []
    for knowledge_point in catalog:
        items = [
            mastery_by_student[student.id][knowledge_point.code]
            for student in students
        ]
        evidenced = [item for item in items if item.mastery is not None]
        confirmed = sum(item.attempt_count for item in evidenced)
        correct = sum(item.correct_count for item in evidenced)
        knowledge_rows.append(
            ClassKnowledgePointAnalytics(
                knowledge_point_id=knowledge_point.id,
                knowledge_point_code=knowledge_point.code,
                knowledge_point_name=knowledge_point.name,
                student_count=len(students),
                evidence_student_count=len(evidenced),
                stable_student_count=sum(
                    item.assessment_status == "assessed" for item in items
                ),
                unassessed_student_count=len(students) - len(evidenced),
                confirmed_attempt_count=confirmed,
                correct_attempt_count=correct,
                incorrect_attempt_count=confirmed - correct,
                accuracy=_ratio(correct, confirmed),
                average_mastery=_mean(
                    [item.mastery for item in evidenced if item.mastery is not None]
                ),
                recent_wrong_count=sum(item.recent_wrong_count for item in evidenced),
                available_question_count=knowledge_point.available_question_count,
            )
        )

    confirmed_attempt_count = sum(row.confirmed_attempt_count for row in knowledge_rows)
    pending_review_count = 0
    invalid_evidence_count = 0
    evidence_student_count = 0
    for context in contexts:
        if any(
            (context.student.id, knowledge_point.id) in context.results
            for knowledge_point in catalog
        ):
            evidence_student_count += 1
        for record in context.records:
            code = _record_knowledge_code(record)
            if record.problem == "needs_review":
                if code in catalog_codes:
                    pending_review_count += 1
            elif record.problem is not None:
                # Missing or unknown snapshot codes cannot safely be assigned to
                # another chapter, so surface them instead of silently hiding them.
                if code in catalog_codes or code is None or code not in all_catalog_codes:
                    invalid_evidence_count += 1

    focus_points: list[ClassFocusPoint] = []
    for row in knowledge_rows:
        if row.evidence_student_count == 0 and row.available_question_count > 0:
            focus_points.append(
                ClassFocusPoint(
                    knowledge_point_code=row.knowledge_point_code,
                    knowledge_point_name=row.knowledge_point_name,
                    category="diagnostic",
                    reason=f"全班 {row.student_count} 名学生暂未评估，可安排基础诊断。",
                )
            )
        elif row.stable_student_count > 0 and row.average_mastery is not None and row.average_mastery < 0.4:
            focus_points.append(
                ClassFocusPoint(
                    knowledge_point_code=row.knowledge_point_code,
                    knowledge_point_name=row.knowledge_point_name,
                    category="practice",
                    reason=(
                        f"{row.evidence_student_count}/{row.student_count} 名学生有证据，"
                        f"掌握度参考均值 {row.average_mastery:.4f}，建议重点练习。"
                    ),
                )
            )
        elif row.recent_wrong_count > 0:
            focus_points.append(
                ClassFocusPoint(
                    knowledge_point_code=row.knowledge_point_code,
                    knowledge_point_name=row.knowledge_point_name,
                    category="recent_errors",
                    reason=(
                        f"各学生最近窗口合计 {row.recent_wrong_count} 次错误，"
                        f"来自 {row.confirmed_attempt_count} 次确认作答。"
                    ),
                )
            )
        elif row.evidence_student_count > row.stable_student_count:
            focus_points.append(
                ClassFocusPoint(
                    knowledge_point_code=row.knowledge_point_code,
                    knowledge_point_name=row.knowledge_point_name,
                    category="observe",
                    reason=(
                        f"{row.evidence_student_count} 名学生已有作答，"
                        f"其中 {row.stable_student_count} 名达到当前证据充足条件。"
                    ),
                )
            )

    focus_priority = {"practice": 0, "recent_errors": 1, "observe": 2, "diagnostic": 3}
    focus_points.sort(
        key=lambda item: (focus_priority[item.category], item.knowledge_point_code)
    )
    covered = sum(row.evidence_student_count > 0 for row in knowledge_rows)
    return ClassOverviewOut(
        class_name=class_name,
        chapter_code=chapter_code,
        computed_at=datetime.now(timezone.utc),
        rule_version=RULE_VERSION,
        summary=ClassAnalyticsSummary(
            student_count=len(students),
            evidence_student_count=evidence_student_count,
            confirmed_attempt_count=confirmed_attempt_count,
            pending_review_count=pending_review_count,
            invalid_evidence_count=invalid_evidence_count,
            covered_knowledge_point_count=covered,
            uncovered_knowledge_point_count=len(knowledge_rows) - covered,
        ),
        knowledge_points=knowledge_rows,
        focus_points=focus_points,
    )


def build_knowledge_point_students(
    db: Session,
    *,
    class_name: str,
    chapter_code: str,
    knowledge_point_code: str,
) -> KnowledgePointStudentListOut:
    students = _active_students(db, class_name)
    if not students:
        raise LookupError("class not found")
    knowledge_point = next(
        (
            item for item in knowledge_point_catalog(db, chapter_code)
            if item.code == knowledge_point_code
        ),
        None,
    )
    if knowledge_point is None:
        raise LookupError("knowledge point not found in chapter")
    rows: list[KnowledgePointStudentAnalytics] = []
    for context in _contexts(db, students):
        item = next(
            value
            for value in mastery_items(context, chapter_code)
            if value.knowledge_point_code == knowledge_point_code
        )
        rows.append(
            KnowledgePointStudentAnalytics(
                student_id=context.student.id,
                student_code=context.student.student_code,
                student_name=context.student.display_name,
                assessment_status=item.assessment_status,
                mastery=item.mastery,
                attempt_count=item.attempt_count,
                correct_count=item.correct_count,
                accuracy=_ratio(item.correct_count, item.attempt_count),
                recent_wrong_count=item.recent_wrong_count,
                last_practiced_at=item.last_practiced_at,
            )
        )
    status_order = {"assessed": 0, "insufficient": 1, "unassessed": 2}
    rows.sort(
        key=lambda item: (
            status_order[item.assessment_status],
            item.mastery if item.mastery is not None else 2.0,
            item.student_code,
        )
    )
    return KnowledgePointStudentListOut(
        class_name=class_name,
        chapter_code=chapter_code,
        knowledge_point_code=knowledge_point.code,
        knowledge_point_name=knowledge_point.name,
        computed_at=datetime.now(timezone.utc),
        items=rows,
    )


def build_assignment_overview(
    db: Session, assignment_id: UUID, *, class_name: str | None = None
) -> AssignmentAnalyticsOut:
    assignment = db.get(Assignment, assignment_id)
    if assignment is None:
        raise LookupError("assignment not found")
    item_counts = (
        select(
            PaperInstanceItem.paper_instance_id.label("paper_id"),
            func.count(PaperInstanceItem.id).label("item_count"),
        )
        .group_by(PaperInstanceItem.paper_instance_id)
        .subquery()
    )
    pending_counts = (
        select(
            Attempt.submission_id.label("submission_id"),
            func.count(Attempt.id).label("pending_count"),
        )
        .where(Attempt.grade_status == "needs_review")
        .group_by(Attempt.submission_id)
        .subquery()
    )
    row_query = (
        select(
            PaperInstance, Student, Submission,
            func.coalesce(item_counts.c.item_count, 0),
            func.coalesce(pending_counts.c.pending_count, 0),
        )
        .join(Student, Student.id == PaperInstance.student_id)
        .outerjoin(Submission, Submission.paper_instance_id == PaperInstance.id)
        .outerjoin(item_counts, item_counts.c.paper_id == PaperInstance.id)
        .outerjoin(
            pending_counts,
            pending_counts.c.submission_id == Submission.id,
        )
        .where(PaperInstance.assignment_id == assignment_id)
    )
    if class_name is not None:
        row_query = row_query.where(
            Student.class_name == class_name,
            Student.archived_at.is_(None),
        )
    rows = db.execute(
        row_query.order_by(Student.student_code, PaperInstance.version_no, PaperInstance.id)
    ).all()
    if class_name is not None and not rows:
        raise LookupError("assignment not found for class")
    student_count = len({row[0].student_id for row in rows})
    draft_count = sum(row[0].status == "draft" for row in rows)
    invalidated_count = sum(row[0].invalidated_at is not None for row in rows)
    active_by_student: dict[UUID, list[tuple]] = {}
    for paper, student, submission, item_count, pending_count in rows:
        if paper.status == "published" and paper.invalidated_at is None:
            active_by_student.setdefault(student.id, []).append(
                (paper, student, submission, item_count, pending_count)
            )
    duplicates = [student_id for student_id, values in active_by_student.items() if len(values) > 1]
    if duplicates:
        raise AnalyticsDataError(
            f"assignment has multiple active published papers for {len(duplicates)} student(s)"
        )

    progress: list[AssignmentProgressStudent] = []
    for values in active_by_student.values():
        paper, student, submission, item_count, pending_count = values[0]
        pending = int(pending_count)
        status = (
            "unsubmitted"
            if submission is None
            else "needs_review"
            if pending
            else "completed"
        )
        progress.append(
            AssignmentProgressStudent(
                paper_instance_id=paper.id,
                student_id=student.id,
                student_code=student.student_code,
                student_name=student.display_name,
                version_no=paper.version_no,
                instance_code=paper.instance_code or "",
                item_count=int(item_count),
                grading_status=status,
                needs_review_count=pending,
                submitted_at=submission.submitted_at if submission is not None else None,
            )
        )
    progress.sort(key=lambda item: item.student_code)
    active_count = len(progress)
    submitted_count = sum(item.grading_status != "unsubmitted" for item in progress)
    needs_review_count = sum(item.grading_status == "needs_review" for item in progress)
    completed_count = sum(item.grading_status == "completed" for item in progress)
    return AssignmentAnalyticsOut(
        assignment_id=assignment.id,
        assignment_title=assignment.title,
        chapter_code=assignment.chapter_code,
        computed_at=datetime.now(timezone.utc),
        summary=AssignmentProgressSummary(
            student_count=student_count,
            draft_count=draft_count,
            active_published_count=active_count,
            historical_invalidated_count=invalidated_count,
            submitted_count=submitted_count,
            unsubmitted_count=active_count - submitted_count,
            needs_review_count=needs_review_count,
            completed_count=completed_count,
            pending_attempt_count=sum(item.needs_review_count for item in progress),
            submission_rate=_ratio(submitted_count, active_count),
            completion_rate=_ratio(completed_count, active_count),
        ),
        students=progress,
    )

