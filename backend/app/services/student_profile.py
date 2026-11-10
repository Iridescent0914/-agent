from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    Attempt, AttemptRevision, KnowledgePoint, PaperInstance, PaperInstanceItem,
    Question, Student, StudentMastery, Submission,
    StudentKnowledgeStatus,
)
from app.schemas.profile import (
    AttemptEvidenceOut, EvidenceRef, ExcludedEvidence, FocusPoint, KnowledgePointOut,
    HistoryPointOut, LiteracyScoreOut, MasteryHistoryOut, MasteryOut, ProfileOut,
    ProfileSummary, RecentWrong,
)
from app.schemas.students import StudentAttemptOut
from app.services.assets import resolve_assets
from app.services.knowledge_catalog import formal_kp
from app.services.mastery import (
    Evidence, MasteryResult, RECENT_WINDOW_SIZE, RULE_VERSION, evidence_problem, replay,
)
from app.services.literacy_scoring import (
    default_computed_literacies,
    effective_literacy_scores_many,
    literacy_outputs_from_scores,
)
from app.services.profile_evidence import (
    EXPECTED_EVIDENCE_EXCLUSIONS, PROFILE_QUESTION_TYPES, attempt_problem,
    load_attempt_rows_many, snapshot_knowledge_code,
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
    knowledge_statuses: dict[UUID, StudentKnowledgeStatus]
    literacies: list[LiteracyScoreOut] = field(
        default_factory=default_computed_literacies
    )
    literacy_values: dict[str, float] = field(default_factory=dict)


def knowledge_point_catalog(
    db: Session, chapter_code: str | None = None, grade: str | None = None
) -> list[KnowledgePointOut]:
    active_chapters = (
        select(Question.chapter_code)
        # Archiving the last active question must not hide past evidence.
        .where(*([Question.grade == grade] if grade is not None else []))
        .distinct()
        .subquery()
    )
    count_filters = [
        Question.question_type.in_(PROFILE_QUESTION_TYPES),
        Question.grading_readiness == 'machine_ready',
        Question.source_review_status == 'reviewed',
        Question.archived_at.is_(None),
    ]
    if grade is not None:
        count_filters.append(Question.grade == grade)
    counts = (
        select(Question.main_knowledge_point_id, func.count(Question.id).label("count"))
        .where(*count_filters)
        .group_by(Question.main_knowledge_point_id).subquery()
    )
    required_filters = [
        Question.question_type.in_(PROFILE_QUESTION_TYPES),
        Question.is_required.is_(True),
        Question.grading_readiness == "machine_ready",
        Question.source_review_status == "reviewed",
        Question.archived_at.is_(None),
    ]
    if grade is not None:
        required_filters.append(Question.grade == grade)
    required_counts = (
        select(
            Question.main_knowledge_point_id,
            func.count(Question.id).label("required_count"),
        )
        .where(*required_filters)
        .group_by(Question.main_knowledge_point_id)
        .subquery()
    )
    query = (
        select(KnowledgePoint, counts.c.count, required_counts.c.required_count)
        # A chapter profile is a curriculum view, not merely a list of points
        # already referenced by questions.  Keep every formal knowledge point
        # in an active chapter so unpractised / currently unstocked points can
        # still be represented as unassessed.
        .outerjoin(
            active_chapters,
            active_chapters.c.chapter_code == KnowledgePoint.chapter_code,
        )
        .outerjoin(counts, counts.c.main_knowledge_point_id == KnowledgePoint.id)
        .outerjoin(
            required_counts,
            required_counts.c.main_knowledge_point_id == KnowledgePoint.id,
        )
    )
    if chapter_code is not None:
        query = query.where(KnowledgePoint.chapter_code == chapter_code)
    if grade is not None:
        query = query.where(active_chapters.c.chapter_code.is_not(None))
    return [
        KnowledgePointOut(
            id=knowledge_point.id,
            code=knowledge_point.code,
            name=knowledge_point.name,
            chapter_code=knowledge_point.chapter_code,
            chapter_name=knowledge_point.chapter_name,
            grade=formal_kp(knowledge_point.code)["grade"],
            section=formal_kp(knowledge_point.code)["section"],
            available_question_count=count or 0,
            required_available_question_count=required_count or 0,
        )
        for knowledge_point, count, required_count in db.execute(
            query.order_by(KnowledgePoint.sort_order, KnowledgePoint.code)
        ).all()
    ]


def _context_from_rows(
    student: Student,
    catalog: list[KnowledgePointOut],
    rows: list[tuple],
    knowledge_statuses: dict[UUID, StudentKnowledgeStatus] | None = None,
) -> ProfileContext:
    by_code = {knowledge_point.code: knowledge_point for knowledge_point in catalog}
    records = []
    for attempt, item, paper, submission, revision in rows:
        problem = None
        evidence = None
        knowledge_point = by_code.get(snapshot_knowledge_code(item))
        main = (
            {"id": knowledge_point.code, "name": knowledge_point.name}
            if knowledge_point is not None
            else {}
        )
        snapshot = item.question_snapshot if item else None
        problem = attempt_problem(
            attempt, item, paper, submission, revision, knowledge_codes=by_code,
        )
        if problem is None:
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
    return ProfileContext(
        student, catalog, records,
        replay([record.evidence for record in records
                if record.problem is None and record.evidence is not None]),
        knowledge_statuses or {},
    )


def load_contexts(
    db: Session, student_ids: list[UUID], *, as_of: datetime | None = None,
    include_literacy: bool = True,
) -> dict[UUID, ProfileContext]:
    ids = list(dict.fromkeys(student_ids))
    if not ids:
        return {}
    students = list(db.scalars(select(Student).where(
        Student.id.in_(ids), Student.archived_at.is_(None)
    )))
    if len(students) != len(ids):
        raise LookupError("one or more students not found")
    catalog = knowledge_point_catalog(db)
    grouped = load_attempt_rows_many(db, ids, as_of=as_of)
    statuses_by_student: dict[UUID, dict[UUID, StudentKnowledgeStatus]] = {
        student.id: {} for student in students
    }
    for status in db.scalars(select(StudentKnowledgeStatus).where(
        StudentKnowledgeStatus.student_id.in_(ids)
    )):
        statuses_by_student[status.student_id][status.knowledge_point_id] = status
    by_student = {
        student.id: _context_from_rows(
            student,
            catalog,
            grouped[student.id],
            statuses_by_student[student.id],
        )
        for student in students
    }
    if not include_literacy:
        return {student_id: by_student[student_id] for student_id in ids}
    literacy_as_of = as_of or datetime.now(timezone.utc)
    literacy_values = effective_literacy_scores_many(
        db, ids, as_of=literacy_as_of
    )
    for student_id, context in by_student.items():
        context.literacy_values = literacy_values[student_id]
        context.literacies = literacy_outputs_from_scores(context.literacy_values)
    return {student_id: by_student[student_id] for student_id in ids}


def load_context(
    db: Session, student_id: UUID, *, as_of: datetime | None = None,
    include_literacy: bool = True,
) -> ProfileContext:
    try:
        return load_contexts(db, [student_id], as_of=as_of, include_literacy=include_literacy)[student_id]
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
            learning_status=(
                context.knowledge_statuses[knowledge_point.id].status
                if knowledge_point.id in context.knowledge_statuses else None
            ),
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
    invalid = [
        record for record in context.records
        if record.problem is not None
        and record.problem not in EXPECTED_EVIDENCE_EXCLUSIONS
    ]
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
            pending_review_count=sum(record.problem == "needs_review" for record in context.records),
            invalid_evidence_count=len(invalid), distinct_question_count=len({r.attempt.question_id for r in valid}),
            assessed_knowledge_point_count=assessed,
            unassessed_knowledge_point_count=len(items) - assessed,
        ), masteries=items, literacies=context.literacies,
        focus_points=focus, recent_errors=recent_errors[:10],
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
    invalid = [
        record for record in context.records
        if record.problem is not None
        and record.problem not in EXPECTED_EVIDENCE_EXCLUSIONS
    ]
    if invalid:
        details = ", ".join(f"{r.attempt.id}:{r.problem}" for r in invalid[:10])
        raise ValueError(f"Cannot rebuild: {len(invalid)} invalid evidence records ({details})")
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
