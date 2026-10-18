from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    Attempt, AttemptRevision, KnowledgePoint, PaperInstance, PaperInstanceItem,
    Question, Student, StudentMastery, Submission,
)
from app.schemas.profile import (
    AttemptEvidenceOut, EvidenceRef, ExcludedEvidence, FocusPoint, KnowledgePointOut,
    HistoryPointOut, MasteryHistoryOut, MasteryOut, ProfileOut, ProfileSummary, RecentWrong,
)
from app.schemas.students import StudentAttemptOut
from app.services.assets import resolve_assets
from app.services.mastery import (
    Evidence, MasteryResult, RECENT_WINDOW_SIZE, RULE_VERSION, evidence_problem, replay,
)


@dataclass
class AttemptRecord:
    attempt: Attempt
    item: PaperInstanceItem | None
    paper: PaperInstance | None
    submission: Submission | None
    revision: AttemptRevision | None
    evidence: Evidence | None
    problem: str | None
    main_knowledge_point: dict[str, str]


@dataclass
class ProfileContext:
    student: Student
    catalog: list[KnowledgePointOut]
    records: list[AttemptRecord]
    results: dict[tuple[UUID, UUID], MasteryResult]


def knowledge_point_catalog(
    db: Session, chapter_code: str | None = None
) -> list[KnowledgePointOut]:
    active = (
        select(Question.main_knowledge_point_id)
        .where(Question.archived_at.is_(None))
        .distinct()
        .subquery()
    )
    counts = (
        select(Question.main_knowledge_point_id, func.count(Question.id).label("count"))
        .where(
            Question.grading_readiness == 'machine_ready',
            Question.source_review_status == 'reviewed',
            Question.archived_at.is_(None),
        )
        .group_by(Question.main_knowledge_point_id).subquery()
    )
    query = (
        select(KnowledgePoint, counts.c.count)
        .join(active, active.c.main_knowledge_point_id == KnowledgePoint.id)
        .outerjoin(counts, counts.c.main_knowledge_point_id == KnowledgePoint.id)
    )
    if chapter_code is not None:
        query = query.where(KnowledgePoint.chapter_code == chapter_code)
    return [
        KnowledgePointOut(
            id=knowledge_point.id,
            code=knowledge_point.code,
            name=knowledge_point.name,
            chapter_code=knowledge_point.chapter_code,
            available_question_count=count or 0,
        )
        for knowledge_point, count in db.execute(
            query.order_by(KnowledgePoint.sort_order, KnowledgePoint.code)
        ).all()
    ]


def _context_from_rows(
    student: Student,
    catalog: list[KnowledgePointOut],
    rows: list[tuple],
    current_knowledge_by_question_id: dict[UUID, tuple[str, str]],
) -> ProfileContext:
    by_code = {knowledge_point.code: knowledge_point for knowledge_point in catalog}
    records = []
    for attempt, item, paper, submission, revision in rows:
        problem = None
        evidence = None
        current = current_knowledge_by_question_id.get(attempt.question_id)
        knowledge_point = by_code.get(current[0]) if current else None
        main = (
            {"id": knowledge_point.code, "name": knowledge_point.name}
            if knowledge_point is not None
            else {}
        )
        snapshot = item.question_snapshot if item else None
        if item is None or paper is None or submission is None:
            problem = "missing_ownership_record"
        elif (attempt.student_id != submission.student_id or attempt.student_id != paper.student_id
              or submission.paper_instance_id != paper.id or attempt.question_id != item.question_id):
            problem = "ownership_mismatch"
        elif paper.status != "published":
            problem = "unpublished_paper"
        elif knowledge_point is None:
            problem = "unmapped_knowledge_point"
        elif (not isinstance(snapshot, dict) or not isinstance(snapshot.get("question_text"), str)
              or not isinstance(snapshot.get("original_qid"), str)
              or snapshot.get("question_type") not in {"choice", "fill_blank", "true_false"}
              or type(snapshot.get("difficulty")) is not int
              or not 1 <= snapshot["difficulty"] <= 5):
            problem = "invalid_question_snapshot"
        elif revision is None:
            problem = "missing_current_revision"
        else:
            evidence = Evidence(
                attempt_id=attempt.id, student_id=student.id, question_id=attempt.question_id,
                paper_instance_item_id=attempt.paper_instance_item_id,
                knowledge_point_id=knowledge_point.id,
                knowledge_point_code=knowledge_point.code,
                answered_at=attempt.answered_at,
                revision_no=attempt.revision_no, grade_status=attempt.grade_status,
                is_correct=attempt.is_correct, question_type=snapshot["question_type"],
                difficulty=snapshot["difficulty"],
            )
            problem = evidence_problem(evidence)
        records.append(AttemptRecord(
            attempt, item, paper, submission, revision, evidence, problem, main
        ))
    return ProfileContext(student, catalog, records,
                          replay([record.evidence for record in records
                                  if record.problem is None and record.evidence is not None]))


def load_contexts(db: Session, student_ids: list[UUID]) -> dict[UUID, ProfileContext]:
    ids = list(dict.fromkeys(student_ids))
    if not ids:
        return {}
    students = list(db.scalars(select(Student).where(
        Student.id.in_(ids), Student.archived_at.is_(None)
    )))
    if len(students) != len(ids):
        raise LookupError("one or more students not found")
    catalog = knowledge_point_catalog(db)
    grouped: dict[UUID, list[tuple]] = {student.id: [] for student in students}
    rows = db.execute(
        select(Attempt, PaperInstanceItem, PaperInstance, Submission, AttemptRevision)
        .outerjoin(PaperInstanceItem, PaperInstanceItem.id == Attempt.paper_instance_item_id)
        .outerjoin(PaperInstance, PaperInstance.id == PaperInstanceItem.paper_instance_id)
        .outerjoin(Submission, Submission.id == Attempt.submission_id)
        .outerjoin(AttemptRevision, (AttemptRevision.attempt_id == Attempt.id)
                   & (AttemptRevision.revision_no == Attempt.revision_no))
        .where(Attempt.student_id.in_(ids))
        .order_by(Attempt.student_id, Attempt.answered_at, Attempt.id)
    ).all()
    for row in rows:
        grouped[row[0].student_id].append(row)
    current_knowledge_by_question_id = {
        question_id: (code, name)
        for question_id, code, name in db.execute(
            select(Question.id, KnowledgePoint.code, KnowledgePoint.name)
            .join(
                KnowledgePoint,
                KnowledgePoint.id == Question.main_knowledge_point_id,
            )
            .where(Question.archived_at.is_(None))
        ).all()
    }
    by_student = {
        student.id: _context_from_rows(
            student,
            catalog,
            grouped[student.id],
            current_knowledge_by_question_id,
        )
        for student in students
    }
    return {student_id: by_student[student_id] for student_id in ids}


def load_context(db: Session, student_id: UUID) -> ProfileContext:
    try:
        return load_contexts(db, [student_id])[student_id]
    except LookupError as exc:
        raise LookupError("student not found") from exc


def mastery_items(context: ProfileContext, chapter_code: str | None = None) -> list[MasteryOut]:
    items = []
    for knowledge_point in context.catalog:
        if chapter_code is not None and knowledge_point.chapter_code != chapter_code:
            continue
        result = context.results.get((context.student.id, knowledge_point.id))
        values = {}
        if result:
            for key in ("attempt_count", "correct_count", "distinct_question_count", "recent_attempt_count",
                        "recent_wrong_count", "last_practiced_at", "first_practiced_at",
                        "practiced_question_types", "practiced_difficulties"):
                values[key] = getattr(result, key)
        items.append(MasteryOut(
            knowledge_point_id=knowledge_point.id,
            knowledge_point_code=knowledge_point.code,
            knowledge_point_name=knowledge_point.name,
            assessment_status=("unassessed" if result is None else "insufficient"
                               if result.attempt_count < 3 or result.distinct_question_count < 2 else "assessed"),
            mastery=float(result.mastery) if result else None,
            confidence_level=result.confidence_level if result else "low",
            available_question_count=knowledge_point.available_question_count, **values,
        ))
    return items


def build_profile(context: ProfileContext, *, computed_at: datetime | None = None) -> ProfileOut:
    items = mastery_items(context)
    focus = []
    for item in items:
        result = context.results.get((context.student.id, item.knowledge_point_id))
        refs = ([EvidenceRef(attempt_id=point.attempt_id, revision_no=point.revision_no)
                 for point in result.history] if result else [])
        category = None
        if item.assessment_status == "unassessed":
            category, reason = "diagnostic", "尚无确认作答，建议安排基础诊断。"
        elif item.assessment_status == "insufficient":
            category, reason = "observe", f"目前有 {item.attempt_count} 次确认作答、{item.distinct_question_count} 道不同题，建议补充证据。"
        elif item.mastery < 0.4:
            category, reason = "practice", f"掌握度参考值 {item.mastery:.4f}，依据 {item.attempt_count} 次确认作答，建议重点练习。"
        elif item.recent_wrong_count / item.recent_attempt_count >= 0.5:
            category, reason = "recent_errors", f"最近 {item.recent_attempt_count} 次确认作答错 {item.recent_wrong_count} 次，建议继续练习。"
            refs = refs[-RECENT_WINDOW_SIZE:]
        if category:
            focus.append(FocusPoint(
                                    knowledge_point_code=item.knowledge_point_code,
                                    knowledge_point_name=item.knowledge_point_name,
                                    category=category, reason=reason, evidence=refs))
    valid = [record for record in context.records if record.problem is None]
    invalid = [record for record in context.records if record.problem not in (None, "needs_review")]
    recent_errors = []
    for record in reversed(valid):
        if record.attempt.is_correct is False:
            main = record.main_knowledge_point
            recent_errors.append(RecentWrong(
                attempt_id=record.attempt.id, revision_no=record.attempt.revision_no,
                answered_at=record.attempt.answered_at, original_qid=record.item.question_snapshot["original_qid"],
                knowledge_point_code=main["id"], knowledge_point_name=main["name"],
            ))
    assessed = sum(item.attempt_count > 0 for item in items)
    return ProfileOut(
        student_id=context.student.id, computed_at=computed_at or datetime.now(timezone.utc),
        rule_version=RULE_VERSION,
        summary=ProfileSummary(
            total_attempt_count=len(context.records), valid_attempt_count=len(valid),
            pending_review_count=sum(record.attempt.grade_status == "needs_review" for record in context.records),
            invalid_evidence_count=len(invalid), distinct_question_count=len({r.attempt.question_id for r in valid}),
            assessed_knowledge_point_count=assessed,
            unassessed_knowledge_point_count=len(items) - assessed,
        ), masteries=items, focus_points=focus, recent_errors=recent_errors[:10],
        excluded_evidence=[ExcludedEvidence(attempt_id=r.attempt.id, revision_no=r.attempt.revision_no,
                                            reason=r.problem) for r in context.records if r.problem],
    )


def history_for(context: ProfileContext, knowledge_point_code: str) -> MasteryHistoryOut:
    item = next(
        (
            item for item in mastery_items(context)
            if item.knowledge_point_code == knowledge_point_code
        ),
        None,
    )
    if item is None:
        raise LookupError("knowledge point not found")
    result = context.results.get((context.student.id, item.knowledge_point_id))
    return MasteryHistoryOut(mastery=item, rule_version=RULE_VERSION,
                             points=[HistoryPointOut(**asdict(point)) for point in result.history] if result else [])


def attempt_summary(record: AttemptRecord) -> StudentAttemptOut:
    attempt, item = record.attempt, record.item
    snapshot = item.question_snapshot if item and isinstance(item.question_snapshot, dict) else {}
    main = record.main_knowledge_point
    return StudentAttemptOut(
        id=attempt.id, answered_at=attempt.answered_at, original_qid=snapshot.get("original_qid", ""),
        question_id=attempt.question_id, question_text=snapshot.get("question_text", ""),
        difficulty=snapshot.get("difficulty") if type(snapshot.get("difficulty")) is int else None,
        main_knowledge_point_code=main.get("id", ""),
        main_knowledge_point_name=main.get("name", ""),
        student_answer=attempt.student_answer, grade_status=attempt.grade_status, is_correct=attempt.is_correct,
        paper_instance_id=item.paper_instance_id if item else None,
        paper_instance_item_id=attempt.paper_instance_item_id, revision_no=attempt.revision_no,
        used_in_profile=record.problem is None, exclusion_reason=record.problem,
    )


def attempt_detail(record: AttemptRecord) -> AttemptEvidenceOut:
    attempt, item, revision = record.attempt, record.item, record.revision
    snapshot = item.question_snapshot if item and isinstance(item.question_snapshot, dict) else {}
    return AttemptEvidenceOut(
        id=attempt.id, revision_no=attempt.revision_no, answered_at=attempt.answered_at,
        reviewed_at=attempt.reviewed_at, question_id=attempt.question_id,
        paper_instance_item_id=attempt.paper_instance_item_id,
        paper_instance_id=item.paper_instance_id if item else None,
        instance_code=record.paper.instance_code if record.paper else None,
        question_snapshot=snapshot, options_snapshot=item.options_snapshot if item else None,
        answer_snapshot=item.answer_snapshot if item else None,
        answer_rule_snapshot=item.answer_rule_snapshot if item else {},
        knowledge_point_snapshot=item.knowledge_point_snapshot if item else {},
        assets=resolve_assets(snapshot.get("assets", [])), student_answer=attempt.student_answer,
        grade_status=attempt.grade_status, is_correct=attempt.is_correct,
        used_in_profile=record.problem is None, exclusion_reason=record.problem,
        revision={"revision_no": revision.revision_no, "before_state": revision.before_state,
                  "after_state": revision.after_state, "reason": revision.reason,
                  "operator_type": revision.operator_type, "created_at": revision.created_at} if revision else None,
    )


MASTERY_FIELDS = ("mastery", "confidence_level", "attempt_count", "correct_count",
                  "recent_wrong_count", "last_practiced_at")


def persist_mastery(db: Session, context: ProfileContext) -> dict[str, int]:
    invalid = [record for record in context.records if record.problem not in (None, "needs_review")]
    if invalid:
        raise ValueError(f"Cannot rebuild: {len(invalid)} invalid evidence records")
    existing = {row.knowledge_point_id: row for row in db.scalars(
        select(StudentMastery).where(StudentMastery.student_id == context.student.id))}
    stats = {"inserted": 0, "updated": 0, "deleted": 0, "unchanged": 0}
    for (_, knowledge_point_id), result in context.results.items():
        values = {key: getattr(result, key) for key in MASTERY_FIELDS}
        row = existing.pop(knowledge_point_id, None)
        if row is None:
            db.add(StudentMastery(
                student_id=context.student.id,
                knowledge_point_id=knowledge_point_id,
                **values,
            ))
            stats["inserted"] += 1
        elif any(getattr(row, key) != value for key, value in values.items()):
            for key, value in values.items():
                setattr(row, key, value)
            stats["updated"] += 1
        else:
            stats["unchanged"] += 1
    for row in existing.values():
        db.delete(row)
        stats["deleted"] += 1
    db.flush()
    return stats
