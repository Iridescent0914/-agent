from __future__ import annotations

from collections import Counter
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import secrets
from uuid import UUID, uuid4

from app.services.profile_evidence import PROFILE_QUESTION_TYPES
from app.services.question_policy import automatic_pool_ready

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    Assignment, Attempt, AttemptRevision, KnowledgePoint, PaperInstance,
    PaperInstanceItem, Question, SchoolClass, Student,
)
from app.schemas.assignments import AssignmentDetailOut, AssignmentListItem
from app.schemas.papers import (
    AlternativeDiagnostic, AlternativeOut, PaperByCodeOut, PaperInstanceStudentOut,
    PaperInstanceTeacherOut, PaperItemOut, PaperSummaryOut, StudentPaperItemOut,
)
from app.schemas.recommendations import (
    PersonalizedItemAudit,
    RecommendationRequest,
    ScoreParts,
)
from app.services.assets import resolve_assets
from app.services.literacy_catalog import parse_literacy_names
from app.services.paper_snapshots import (
    copy_item, published_knowledge_point_snapshot, current_selection_audit, load_question, load_questions,
    snapshot_payload, student_safe_question_snapshot, student_safe_value,
)
from app.services.profile_refresh import refresh_student_profiles
from app.services.literacy_catalog import normalize_literacy_label
from app.services.recommendations import (
    Candidate, history_source_question_ids, recent_completed_assignment_ids,
    score_candidate,
)
from app.services.student_profile import load_context


class PaperConflict(RuntimeError):
    pass


class PaperValidation(ValueError):
    pass


CONFIRMED_GRADE_STATUSES = frozenset({"auto_confirmed", "teacher_confirmed"})
GRADED_WITHDRAW_BLOCK_REASON = "该试卷已有批改结果，不能撤回"


def paper_items(db: Session, paper_id: UUID) -> list[PaperInstanceItem]:
    return list(db.scalars(
        select(PaperInstanceItem)
        .where(PaperInstanceItem.paper_instance_id == paper_id)
        .order_by(PaperInstanceItem.order_no, PaperInstanceItem.id)
    ))


def _assignment_question_usage_count(
    db: Session,
    *,
    assignment_id: UUID,
    question_id: UUID,
    include_student_id: UUID,
) -> int:
    """Snapshot how many students currently use a question in an assignment.

    Distinct students prevent an active published version and its draft revision
    from being counted twice. Invalidated historical versions are excluded.
    ``include_student_id`` represents the replacement that is about to be saved.
    """

    student_ids = set(db.scalars(
        select(PaperInstance.student_id)
        .join(
            PaperInstanceItem,
            PaperInstanceItem.paper_instance_id == PaperInstance.id,
        )
        .where(
            PaperInstance.assignment_id == assignment_id,
            PaperInstance.invalidated_at.is_(None),
            PaperInstanceItem.question_id == question_id,
        )
        .distinct()
    ))
    student_ids.add(include_student_id)
    return len(student_ids)


def _paper(db: Session, paper_id: UUID, *, lock: bool = False) -> PaperInstance:
    query = select(PaperInstance).where(PaperInstance.id == paper_id)
    if lock:
        query = query.with_for_update()
    paper = db.scalar(query)
    if paper is None:
        raise LookupError("paper instance not found")
    return paper


def _draft(paper: PaperInstance, expected_revision: int) -> None:
    if paper.status != "draft":
        raise PaperConflict("published paper is immutable")
    if paper.edit_revision != expected_revision:
        raise PaperConflict("draft was changed; refresh before editing")


def _event(paper: PaperInstance, event_type: str, details: dict) -> None:
    before = paper.edit_revision
    paper.edit_revision += 1
    history = list(paper.edit_history or [])
    history.append({
        "event_id": str(uuid4()), "type": event_type,
        "at": datetime.now(timezone.utc).isoformat(), "actor": "demo_teacher",
        "before_revision": before, "after_revision": paper.edit_revision,
        "details": details,
    })
    paper.edit_history = history


def _graded_paper_ids(db: Session, paper_ids: list[UUID]) -> set[UUID]:
    """Return papers that currently or historically contain confirmed grading."""

    if not paper_ids:
        return set()
    rows = db.execute(
        select(
            PaperInstanceItem.paper_instance_id,
            Attempt.id,
            Attempt.grade_status,
        )
        .join(Attempt, Attempt.paper_instance_item_id == PaperInstanceItem.id)
        .where(PaperInstanceItem.paper_instance_id.in_(paper_ids))
    ).all()
    graded = {
        paper_id
        for paper_id, _attempt_id, grade_status in rows
        if grade_status in CONFIRMED_GRADE_STATUSES
    }
    unresolved_attempt_to_paper = {
        attempt_id: paper_id
        for paper_id, attempt_id, grade_status in rows
        if grade_status not in CONFIRMED_GRADE_STATUSES
    }
    if unresolved_attempt_to_paper:
        revisions = db.execute(
            select(AttemptRevision.attempt_id, AttemptRevision.after_state)
            .where(
                AttemptRevision.attempt_id.in_(
                    list(unresolved_attempt_to_paper)
                )
            )
        ).all()
        graded.update(
            unresolved_attempt_to_paper[attempt_id]
            for attempt_id, after_state in revisions
            if isinstance(after_state, dict)
            and after_state.get("grade_status") in CONFIRMED_GRADE_STATUSES
        )
    return graded


def _lock_and_check_graded_attempts(db: Session, paper_id: UUID) -> bool:
    """Serialize with attempt review and preserve historical confirmed states."""

    attempts = list(db.scalars(
        select(Attempt)
        .join(
            PaperInstanceItem,
            PaperInstanceItem.id == Attempt.paper_instance_item_id,
        )
        .where(PaperInstanceItem.paper_instance_id == paper_id)
        .order_by(Attempt.id)
        .with_for_update()
    ))
    if any(
        attempt.grade_status in CONFIRMED_GRADE_STATUSES
        for attempt in attempts
    ):
        return True
    if not attempts:
        return False
    revision_states = db.scalars(
        select(AttemptRevision.after_state).where(
            AttemptRevision.attempt_id.in_([attempt.id for attempt in attempts])
        )
    )
    return any(
        isinstance(after_state, dict)
        and after_state.get("grade_status") in CONFIRMED_GRADE_STATUSES
        for after_state in revision_states
    )


def _summary(
    db: Session,
    paper: PaperInstance,
    student: Student | None = None,
    item_count: int | None = None,
    has_graded_attempts: bool | None = None,
) -> PaperSummaryOut:
    student = student or db.get(Student, paper.student_id)
    if student is None:
        raise LookupError("paper student not found")
    count = item_count if item_count is not None else int(db.scalar(
        select(func.count(PaperInstanceItem.id)).where(PaperInstanceItem.paper_instance_id == paper.id)
    ) or 0)
    graded = (
        has_graded_attempts
        if has_graded_attempts is not None
        else paper.id in _graded_paper_ids(db, [paper.id])
    )
    is_active = paper.status == 'published' and paper.invalidated_at is None
    audit = paper.recommendation_audit or {}
    return PaperSummaryOut(
        id=paper.id, assignment_id=paper.assignment_id, student_id=paper.student_id,
        student_code=student.student_code, student_name=student.display_name,
        version_no=paper.version_no, status=paper.status, item_count=count,
        requested_count=audit.get("requested_count"), shortfall=audit.get("shortfall"),
        edit_revision=paper.edit_revision, instance_code=paper.instance_code,
        supersedes_instance_id=paper.supersedes_instance_id,
        superseded_by_instance_id=paper.superseded_by_instance_id,
        is_active=is_active, has_graded_attempts=graded,
        can_withdraw=is_active and not graded,
        withdraw_block_reason=(
            GRADED_WITHDRAW_BLOCK_REASON if is_active and graded else None
        ),
        invalidated_at=paper.invalidated_at, invalidation_type=paper.invalidation_type,
        invalidation_reason=paper.invalidation_reason, invalidated_by=paper.invalidated_by,
        created_at=paper.created_at, published_at=paper.published_at,
    )


def paper_summary(db: Session, paper: PaperInstance) -> PaperSummaryOut:
    return _summary(db, paper)


def teacher_view(db: Session, paper_id: UUID) -> PaperInstanceTeacherOut:
    paper = _paper(db, paper_id)
    items = paper_items(db, paper.id)
    summary = _summary(db, paper, item_count=len(items))
    output = []
    for item in items:
        current_snapshot = published_knowledge_point_snapshot(
            db, item.question_id, item.knowledge_point_snapshot
        )
        output.append(PaperItemOut(
            id=item.id, question_id=item.question_id, order_no=item.order_no,
            question_snapshot=item.question_snapshot, options_snapshot=item.options_snapshot,
            answer_snapshot=item.answer_snapshot, answer_rule_snapshot=item.answer_rule_snapshot,
            knowledge_point_snapshot=current_snapshot,
            assets=resolve_assets(item.question_snapshot.get("assets", [])),
            selection_audit=current_selection_audit(item.selection_audit, current_snapshot),
        ))
    return PaperInstanceTeacherOut(
        **summary.model_dump(), recommendation_audit=paper.recommendation_audit,
        edit_history=paper.edit_history or [], items=output,
    )


def student_view(db: Session, paper_id: UUID) -> PaperInstanceStudentOut:
    paper = _paper(db, paper_id)
    student = db.get(Student, paper.student_id)
    if student is None:
        raise LookupError("paper student not found")
    items = []
    for item in paper_items(db, paper.id):
        items.append(StudentPaperItemOut(
            id=item.id, order_no=item.order_no,
            question_snapshot=student_safe_question_snapshot(item.question_snapshot),
            options_snapshot=student_safe_value(item.options_snapshot),
            knowledge_point_snapshot=published_knowledge_point_snapshot(
                db, item.question_id, item.knowledge_point_snapshot
            ),
            assets=resolve_assets(item.question_snapshot.get("assets", [])),
        ))
    return PaperInstanceStudentOut(
        id=paper.id, assignment_id=paper.assignment_id, student_id=paper.student_id,
        student_code=student.student_code, version_no=paper.version_no,
        status=paper.status, instance_code=paper.instance_code,
        is_active=paper.status == 'published' and paper.invalidated_at is None,
        invalidated_at=paper.invalidated_at, invalidation_type=paper.invalidation_type,
        invalidation_reason=paper.invalidation_reason,
        superseded_by_instance_id=paper.superseded_by_instance_id, items=items,
    )


def assignment_detail(
    db: Session, assignment_id: UUID, *, include_papers: bool = True
) -> AssignmentDetailOut:
    assignment = db.get(Assignment, assignment_id)
    if assignment is None:
        raise LookupError("assignment not found")
    summaries = []
    if include_papers:
        summaries, _ = assignment_paper_summaries(
            db, assignment.id, offset=0, limit=None
        )
    counts = db.execute(select(
        func.count(func.distinct(PaperInstance.student_id)),
        func.count(PaperInstance.id).filter(PaperInstance.status == "draft"),
        func.count(PaperInstance.id).filter(
            PaperInstance.status == "published",
            PaperInstance.invalidated_at.is_(None),
        ),
        func.count(PaperInstance.id).filter(PaperInstance.invalidated_at.is_not(None)),
    ).where(PaperInstance.assignment_id == assignment.id)).one()
    school_class = db.get(SchoolClass, assignment.class_id) if assignment.class_id else None
    return AssignmentDetailOut(
        id=assignment.id, title=assignment.title, chapter_code=assignment.chapter_code,
        class_id=assignment.class_id,
        class_name=school_class.name if school_class else None,
        assignment_type=assignment.assignment_type,
        student_count=int(counts[0] or 0),
        draft_count=int(counts[1] or 0),
        published_count=int(counts[2] or 0),
        invalidated_count=int(counts[3] or 0),
        created_at=assignment.created_at, teaching_scope=assignment.teaching_scope,
        generation_config=assignment.generation_config, papers=summaries,
    )


def assignment_paper_summaries(
    db: Session, assignment_id: UUID, *, offset: int = 0, limit: int | None = 50
) -> tuple[list[PaperSummaryOut], int]:
    if db.get(Assignment, assignment_id) is None:
        raise LookupError("assignment not found")
    total = int(db.scalar(select(func.count(PaperInstance.id)).where(
        PaperInstance.assignment_id == assignment_id
    )) or 0)
    query = (
        select(PaperInstance, Student, func.count(PaperInstanceItem.id))
        .join(Student, Student.id == PaperInstance.student_id)
        .outerjoin(
            PaperInstanceItem,
            PaperInstanceItem.paper_instance_id == PaperInstance.id,
        )
        .where(PaperInstance.assignment_id == assignment_id)
        .group_by(PaperInstance.id, Student.id)
        .order_by(Student.student_code, PaperInstance.version_no, PaperInstance.id)
        .offset(offset)
    )
    if limit is not None:
        query = query.limit(limit)
    rows = db.execute(query).all()
    graded_paper_ids = _graded_paper_ids(
        db, [paper.id for paper, _student, _item_count in rows]
    )
    return [
        _summary(
            db,
            paper,
            student=student,
            item_count=int(item_count or 0),
            has_graded_attempts=paper.id in graded_paper_ids,
        )
        for paper, student, item_count in rows
    ], total


def list_assignment_items(
    db: Session, *, offset: int, limit: int, class_name: str | None = None,
    chapter_code: str | None = None, class_id: UUID | None = None,
) -> tuple[list[AssignmentListItem], int]:
    assignment_query = select(Assignment).where(
        Assignment.assignment_type.in_(("practice", "weekly_summary"))
    )
    if class_id is not None:
        assignment_query = assignment_query.where(Assignment.class_id == class_id)
    if chapter_code is not None:
        assignment_query = assignment_query.where(Assignment.chapter_code == chapter_code)
    if class_name is not None:
        class_assignment_ids = (
            select(PaperInstance.assignment_id)
            .join(Student, Student.id == PaperInstance.student_id)
            .where(Student.class_name == class_name, Student.archived_at.is_(None))
            .distinct()
        )
        assignment_query = assignment_query.where(Assignment.id.in_(class_assignment_ids))
    total = int(
        db.scalar(select(func.count()).select_from(assignment_query.subquery())) or 0
    )
    assignments = list(db.scalars(
        assignment_query.order_by(Assignment.created_at.desc(), Assignment.id)
        .offset(offset).limit(limit)
    ))
    assignment_ids = [assignment.id for assignment in assignments]
    paper_query = select(PaperInstance)
    if assignment_ids:
        paper_query = paper_query.where(PaperInstance.assignment_id.in_(assignment_ids))
    if class_name is not None:
        paper_query = (
            paper_query.join(Student, Student.id == PaperInstance.student_id)
            .where(Student.class_name == class_name, Student.archived_at.is_(None))
        )
    papers_by_assignment: dict[UUID, list[PaperInstance]] = {
        assignment_id: [] for assignment_id in assignment_ids
    }
    if assignment_ids:
        for paper in db.scalars(paper_query):
            papers_by_assignment[paper.assignment_id].append(paper)
    class_ids = {assignment.class_id for assignment in assignments if assignment.class_id}
    classes = {
        item.id: item for item in db.scalars(
            select(SchoolClass).where(SchoolClass.id.in_(class_ids))
        )
    } if class_ids else {}
    result = []
    for assignment in assignments:
        school_class = classes.get(assignment.class_id)
        papers = papers_by_assignment[assignment.id]
        result.append(AssignmentListItem(
            id=assignment.id, title=assignment.title, chapter_code=assignment.chapter_code,
            class_id=assignment.class_id,
            class_name=school_class.name if school_class else None,
            assignment_type=assignment.assignment_type,
            student_count=len({paper.student_id for paper in papers}),
            draft_count=sum(paper.status == "draft" for paper in papers),
            published_count=sum(
                paper.status == 'published' and paper.invalidated_at is None for paper in papers
            ),
            invalidated_count=sum(paper.invalidated_at is not None for paper in papers),
            created_at=assignment.created_at,
        ))
    return result, total


def list_recent_completed_assignments(
    db: Session, *, class_id: UUID, limit: int = 5,
) -> list[AssignmentListItem]:
    """Return the latest fully graded assignments for the selected class.

    A teacher explicitly chooses the history source; the API deliberately does
    not guess which assignment a relative label such as "yesterday" refers to.
    """

    eligible_ids = set(recent_completed_assignment_ids(
        db, class_id=class_id, limit=limit,
    ))
    completed: list[AssignmentListItem] = []
    offset = 0
    while len(completed) < limit:
        candidates, total = list_assignment_items(
            db, offset=offset, limit=100, class_id=class_id,
        )
        for item in candidates:
            if item.id in eligible_ids:
                completed.append(item)
                if len(completed) == limit:
                    break
        offset += len(candidates)
        if not candidates or offset >= total:
            break
    return completed


def delete_empty_assignment(db: Session, assignment_id: UUID) -> UUID:
    assignment = db.scalar(
        select(Assignment).where(Assignment.id == assignment_id).with_for_update()
    )
    if assignment is None:
        raise LookupError("assignment not found")
    paper_count = int(
        db.scalar(
            select(func.count(PaperInstance.id)).where(
                PaperInstance.assignment_id == assignment_id
            )
        )
        or 0
    )
    if paper_count:
        raise PaperConflict("作业中仍有试卷，请先处理试卷后再删除作业")
    db.delete(assignment)
    db.flush()
    return assignment_id


def _parameters(db: Session, paper: PaperInstance) -> tuple[RecommendationRequest, datetime]:
    assignment = db.get(Assignment, paper.assignment_id)
    config = (assignment.generation_config if assignment else None) or {}
    raw = config.get("recommendation_parameters")
    as_of = config.get("as_of")
    if not raw or not as_of:
        raise PaperValidation("paper has no assignment generation configuration")
    return RecommendationRequest.model_validate(raw), datetime.fromisoformat(as_of)


def _review_source_assignment_ids(db: Session, paper: PaperInstance) -> list[UUID]:
    assignment = db.get(Assignment, paper.assignment_id)
    config = (assignment.generation_config if assignment else None) or {}
    filter_config = config.get("filter_config") or {}
    history_config = filter_config.get("history") or {}
    raw_values = [history_config.get("assignment_id")]
    raw_values.extend(config.get("source_assignment_ids") or [])
    result = []
    for raw in raw_values:
        try:
            value = UUID(str(raw)) if raw else None
        except (TypeError, ValueError):
            continue
        if value is not None and value not in result:
            result.append(value)
    return result


def alternatives(db: Session, paper_id: UUID, item_id: UUID) -> AlternativeOut:
    paper = _paper(db, paper_id)
    if paper.status != "draft":
        raise PaperConflict("published paper is immutable")
    items = paper_items(db, paper.id)
    target = next((item for item in items if item.id == item_id), None)
    if target is None:
        raise LookupError("paper item not found")
    audit = target.selection_audit or {}
    bucket = audit.get("bucket")
    if bucket not in ("new", "review"):
        raise PaperValidation("paper item has no recommendation bucket")
    params, as_of = _parameters(db, paper)
    context = load_context(db, paper.student_id)
    source_module = audit.get("source_module")
    is_history = source_module == "history"
    is_fixed_review = source_module in {"history", "weekly_summary"}
    is_personalized = source_module == "personalized"
    personalized_audit = (
        PersonalizedItemAudit.model_validate(audit["personalized_audit"])
        if is_personalized and audit.get("personalized_audit")
        else None
    )
    target_knowledge_code = target.knowledge_point_snapshot.get("id")
    target_difficulty = target.question_snapshot.get("difficulty")
    assignment = db.get(Assignment, paper.assignment_id)
    generation_config = (assignment.generation_config if assignment else None) or {}
    personalized_scope = (
        (generation_config.get("filter_config") or {})
        .get("personalized", {})
        .get("knowledge_point_codes", [])
    )
    allowed_codes = (
        [target_knowledge_code]
        if is_fixed_review and target_knowledge_code
        else personalized_scope
        if is_personalized
        else params.new_knowledge_point_codes
        if bucket == "new"
        else params.review_knowledge_point_codes
    )
    existing_ids = {item.question_id for item in items}
    source_question_ids: set[UUID] = set()
    if is_fixed_review:
        for source_assignment_id in _review_source_assignment_ids(db, paper):
            source_question_ids.update(history_source_question_ids(
                db,
                source_assignment_id=source_assignment_id,
                student_ids=[paper.student_id],
            )[paper.student_id])
    if is_history:
        # Keep evidence-based exclusion for legacy drafts that predate the
        # stage-two filter_config contract.
        attempt_ids: list[UUID] = []
        for evidence in audit.get("evidence") or []:
            try:
                attempt_ids.append(UUID(str(evidence.get("attempt_id"))))
            except (AttributeError, TypeError, ValueError):
                continue
        if attempt_ids:
            source_question_ids.update(db.scalars(
                select(Attempt.question_id).where(Attempt.id.in_(attempt_ids))
            ))
    per_knowledge = Counter(
        item.knowledge_point_snapshot.get("id")
        for item in items
        if item.id != target.id
        and (item.selection_audit or {}).get("bucket") == bucket
    )
    practiced = {record.attempt.question_id for record in context.records
                 if params.avoid_recent_days and
                 as_of - timedelta(days=params.avoid_recent_days) <= record.attempt.answered_at <= as_of}
    if params.avoid_recent_days:
        recent_cutoff = as_of - timedelta(days=params.avoid_recent_days)
        practiced.update(db.scalars(
            select(PaperInstanceItem.question_id)
            .join(
                PaperInstance,
                PaperInstance.id == PaperInstanceItem.paper_instance_id,
            )
            .join(Assignment, Assignment.id == PaperInstance.assignment_id)
            .where(
                PaperInstance.student_id == paper.student_id,
                PaperInstance.id != paper.id,
                Assignment.created_at >= recent_cutoff,
                Assignment.created_at <= as_of,
            )
        ))
    question_filters = [Question.archived_at.is_(None), Question.question_type.in_(PROFILE_QUESTION_TYPES)]
    if not is_fixed_review:
        question_filters.append(Question.chapter_code.in_(
            params.chapter_codes or [params.chapter_code]
        ))
    if not is_fixed_review and params.grade_codes:
        question_filters.append(Question.grade.in_(params.grade_codes))
    rows = db.execute(
        select(Question, KnowledgePoint)
        .join(KnowledgePoint, KnowledgePoint.id == Question.main_knowledge_point_id)
        .where(*question_filters)
    ).all()
    ranked = []
    cap = max(
        1,
        int(
            (params.new_count + params.review_count)
            * params.max_knowledge_point_ratio
        ),
    )
    scope_candidate_count = 0
    literacy_matched_count = 0
    ready_candidate_count = 0
    excluded_current_paper_count = 0
    excluded_recent_count = 0
    for question, knowledge_point in rows:
        if knowledge_point.code not in allowed_codes:
            continue
        if is_personalized:
            if question.difficulty != target_difficulty:
                continue
            scope_candidate_count += 1
            if (
                personalized_audit is None
                or personalized_audit.literacy_name
                not in (knowledge_point.core_competencies or [])
            ):
                continue
            try:
                question_literacies = parse_literacy_names(question.core_literacy)
            except ValueError:
                continue
            if personalized_audit.literacy_name not in question_literacies:
                continue
            literacy_matched_count += 1
            if (
                question.grading_readiness != "machine_ready"
                or question.source_review_status != "reviewed"
            ):
                continue
            ready_candidate_count += 1
            if question.id in existing_ids or question.id in source_question_ids:
                excluded_current_paper_count += 1
                continue
            if question.id in practiced:
                excluded_recent_count += 1
                continue
        else:
            if question.id in existing_ids or question.id in source_question_ids:
                continue
            if not is_fixed_review and question.id in practiced:
                continue
        if is_fixed_review:
            if question.difficulty != target_difficulty:
                continue
        elif not is_personalized and (
            question.question_type not in params.question_types
            or not params.min_difficulty <= question.difficulty <= params.max_difficulty
        ):
            continue
        if source_module == "required" and not question.is_required:
            continue
        if (
            not is_personalized
            and (
                question.grading_readiness != "machine_ready"
                or question.source_review_status != "reviewed"
            )
        ):
            continue
        if (
            not is_fixed_review
            and not is_personalized
            and per_knowledge[knowledge_point.code] >= cap
        ):
            continue
        candidate = Candidate(
            question.id, question.original_qid, question.question_type, question.difficulty,
            knowledge_point.id, knowledge_point.code, knowledge_point.name,
        )
        scored = score_candidate(
            candidate,
            context.results.get((paper.student_id, knowledge_point.id)),
            params,
            bucket,
            as_of,
        )
        if is_personalized:
            if personalized_audit is None:
                raise PaperValidation("个性化题缺少生成审计，无法安全换题")
            zero_parts = ScoreParts(
                weakness=0.0,
                recent_wrong=0.0,
                difficulty_match=0.0,
                forgetting=0.0,
            )
            scored = scored.model_copy(update={
                "strategy": "personalized",
                "source_module": "personalized",
                "score": 1.0,
                "components": zero_parts,
                "contributions": zero_parts,
                "target_difficulty": target_difficulty,
                "reason_codes": [
                    "personalized_literacy_score",
                    "teacher_knowledge_scope",
                    "exact_target_difficulty",
                    "teacher_replacement_candidate",
                ],
                "reason_text": (
                    f"基于{personalized_audit.literacy_name}当前整数分"
                    f"{personalized_audit.literacy_score}，基础"
                    f"{personalized_audit.base_star}星；本题用于替换第"
                    f"{personalized_audit.slot_no}个性化题位，保持"
                    f"{personalized_audit.target_star}星和教师所选知识点范围。"
                ),
                "evidence": [],
                "personalized_audit": personalized_audit,
            })
        ranked.append(scored)
    ranked.sort(key=lambda item: (-item.score, item.original_qid, item.question_id.int))
    diagnostic = None
    if is_personalized:
        reason_codes: list[str] = []
        if literacy_matched_count == 0:
            reason_codes.append("no_literacy_matched_exact_difficulty")
        elif ready_candidate_count == 0:
            reason_codes.append("no_machine_ready_candidates")
        if excluded_current_paper_count:
            reason_codes.append("excluded_by_current_paper")
        if excluded_recent_count:
            reason_codes.append("excluded_by_recent_attempts")
        if ranked:
            message = (
                f"找到 {len(ranked)} 道同主要素养、同星级且可机判的替换题。"
            )
        elif literacy_matched_count == 0:
            message = "教师范围内没有同时匹配主要素养与目标星级的题目。"
        elif ready_candidate_count == 0:
            message = "匹配题目尚未达到可机判且已审核状态。"
        elif excluded_recent_count or excluded_current_paper_count:
            message = "匹配题目已被当前试卷占用，或属于该生近期已布置或作答题。"
        else:
            message = "当前严格约束下没有可替换题目。"
        diagnostic = AlternativeDiagnostic(
            source_module="personalized",
            target_difficulty=target_difficulty,
            literacy_name=(
                personalized_audit.literacy_name if personalized_audit else None
            ),
            scope_candidate_count=scope_candidate_count,
            literacy_matched_count=literacy_matched_count,
            ready_candidate_count=ready_candidate_count,
            excluded_current_paper_count=excluded_current_paper_count,
            excluded_recent_count=excluded_recent_count,
            available_candidate_count=len(ranked),
            reason_codes=reason_codes,
            message=message,
        )
    return AlternativeOut(items=ranked[:20], diagnostic=diagnostic)


def replace_item(db: Session, paper_id: UUID, item_id: UUID, *, replacement_id: UUID,
                 expected_revision: int) -> PaperInstanceTeacherOut:
    paper = _paper(db, paper_id, lock=True)
    _draft(paper, expected_revision)
    item = db.get(PaperInstanceItem, item_id)
    if item is None or item.paper_instance_id != paper.id:
        raise LookupError("paper item not found")
    choice = next((value for value in alternatives(db, paper.id, item.id).items
                   if value.question_id == replacement_id), None)
    if choice is None:
        raise PaperValidation("replacement question is not an allowed alternative")
    previous_audit = deepcopy(item.selection_audit or {})
    old = {"item_id": str(item.id), "question_id": str(item.question_id),
           "original_qid": item.question_snapshot.get("original_qid"),
           "source_module": previous_audit.get("source_module")}
    payload = snapshot_payload(load_question(db, replacement_id))
    item.question_id = replacement_id
    item.question_snapshot = payload["question_snapshot"]
    item.options_snapshot = payload["options_snapshot"]
    item.answer_snapshot = payload["answer_snapshot"]
    item.answer_rule_snapshot = payload["answer_rule_snapshot"]
    item.knowledge_point_snapshot = payload["knowledge_point_snapshot"]
    item.answer_region = None
    choice_audit = choice.model_dump(mode="json")
    replacement_reason_text = choice_audit.get("reason_text", "")
    replacement_reason_codes = deepcopy(choice_audit.get("reason_codes") or [])
    previous_module = previous_audit.get("source_module")
    if previous_module in {
        "required", "personalized", "history", "weekly_summary", "legacy_recommendation"
    }:
        choice_audit["source_module"] = previous_module
    reason_codes = list(dict.fromkeys([
        *(choice_audit.get("reason_codes") or []),
        *(previous_audit.get("reason_codes") or []),
        "teacher_replacement",
    ]))
    if previous_module == "required" and "required_question" not in reason_codes:
        reason_codes.append("required_question")
    choice_audit["reason_codes"] = reason_codes
    if previous_module in {"history", "weekly_summary"}:
        choice_audit["evidence"] = deepcopy(previous_audit.get("evidence") or [])
        choice_audit["reason_text"] = previous_audit.get(
            "reason_text", choice_audit.get("reason_text", "")
        )
    if previous_module == "personalized":
        replaced_at = datetime.now(timezone.utc).isoformat()
        personalized_audit = deepcopy(previous_audit.get("personalized_audit"))
        initial_selection = deepcopy(previous_audit.get("initial_selection"))
        if initial_selection is None:
            initial_selection = {
                "question_id": old["question_id"],
                "original_qid": old["original_qid"],
                "reason_text": previous_audit.get("reason_text", ""),
                "reason_codes": deepcopy(previous_audit.get("reason_codes") or []),
                "selected_at": previous_audit.get("selected_at"),
                "class_usage_count": (
                    personalized_audit.get("class_usage_count", 1)
                    if isinstance(personalized_audit, dict)
                    else 1
                ),
            }
        replacement_history = deepcopy(
            previous_audit.get("replacement_history") or []
        )
        replacement_history.append({
            "sequence_no": len(replacement_history) + 1,
            "replaced_at": replaced_at,
            "previous_question_id": old["question_id"],
            "previous_original_qid": old["original_qid"],
            "replacement_question_id": str(replacement_id),
            "replacement_original_qid": payload["question_snapshot"].get(
                "original_qid"
            ),
            "reason_text": replacement_reason_text,
            "reason_codes": replacement_reason_codes,
            "class_usage_count_at_replacement": (
                _assignment_question_usage_count(
                    db,
                    assignment_id=paper.assignment_id,
                    question_id=replacement_id,
                    include_student_id=paper.student_id,
                )
            ),
        })
        choice_audit["personalized_audit"] = deepcopy(
            personalized_audit
        )
        choice_audit["initial_selection"] = initial_selection
        choice_audit["replacement_history"] = replacement_history
    item.selection_audit = {
        "schema_version": "m3-selection-audit-v1", "source": "teacher_replacement",
        "selected_at": datetime.now(timezone.utc).isoformat(), "replaces": old,
        **choice_audit,
    }
    _event(paper, "replace_item", {"item_id": str(item.id), "before": old,
                                    "replacement_question_id": str(replacement_id)})
    db.flush()
    return teacher_view(db, paper.id)


def _renumber(db: Session, items: list[PaperInstanceItem], ordered_ids: list[UUID]) -> None:
    by_id = {item.id: item for item in items}
    offset = len(items) + 100
    for item in items:
        item.order_no += offset
    db.flush()
    for order_no, item_id in enumerate(ordered_ids, start=1):
        by_id[item_id].order_no = order_no
    db.flush()


def delete_item(db: Session, paper_id: UUID, item_id: UUID, *, expected_revision: int) -> PaperInstanceTeacherOut:
    paper = _paper(db, paper_id, lock=True)
    _draft(paper, expected_revision)
    items = paper_items(db, paper.id)
    target = next((item for item in items if item.id == item_id), None)
    if target is None:
        raise LookupError("paper item not found")
    if (target.selection_audit or {}).get("source_module") == "personalized":
        raise PaperValidation("个性化部分固定为 3 道；请使用换题，不能直接删除")
    if len(items) <= 1:
        raise PaperValidation("a paper must keep at least one item")
    details = {"item_id": str(target.id), "question_id": str(target.question_id),
               "order_no": target.order_no, "selection_audit": target.selection_audit}
    db.delete(target)
    db.flush()
    remaining = [item for item in items if item.id != target.id]
    _renumber(db, remaining, [item.id for item in remaining])
    _event(paper, "delete_item", details)
    db.flush()
    return teacher_view(db, paper.id)


def reorder_items(db: Session, paper_id: UUID, item_ids: list[UUID], *,
                  expected_revision: int) -> PaperInstanceTeacherOut:
    paper = _paper(db, paper_id, lock=True)
    _draft(paper, expected_revision)
    items = paper_items(db, paper.id)
    if len(item_ids) != len(items) or set(item_ids) != {item.id for item in items}:
        raise PaperValidation("item_ids must be an exact permutation of the paper items")
    before = [str(item.id) for item in items]
    _renumber(db, items, item_ids)
    _event(paper, "reorder_items", {"before": before, "after": [str(value) for value in item_ids]})
    db.flush()
    return teacher_view(db, paper.id)


CODE_ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"


def _new_code(db: Session) -> str:
    for _ in range(20):
        token = "".join(secrets.choice(CODE_ALPHABET) for _ in range(8))
        code = f"RAT-{token[:4]}-{token[4:]}"
        if db.scalar(select(PaperInstance.id).where(PaperInstance.instance_code == code)) is None:
            return code
    raise RuntimeError("could not allocate a unique instance code")


def delete_draft(db: Session, paper_id: UUID, *, expected_revision: int) -> UUID:
    paper = _paper(db, paper_id, lock=True)
    _draft(paper, expected_revision)
    deleted_id = paper.id
    db.delete(paper)
    db.flush()
    return deleted_id


def _supersede_paper(source: PaperInstance, target: PaperInstance) -> None:
    if source.status != 'published':
        raise PaperConflict('only a published paper can be superseded')
    if source.assignment_id != target.assignment_id or source.student_id != target.student_id:
        raise PaperValidation('superseding version ownership does not match')
    if source.superseded_by_instance_id not in (None, target.id):
        raise PaperConflict('paper was already superseded by another version')

    source.superseded_by_instance_id = target.id
    if source.invalidated_at is None:
        source.invalidated_at = datetime.now(timezone.utc)
        source.invalidation_type = 'superseded'
        source.invalidation_reason = f'版本 v{target.version_no} 发布后自动作废'
        source.invalidated_by = 'demo_teacher'
        event_type = 'superseded'
    else:
        event_type = 'link_superseding_version'
    _event(source, event_type, {
        'superseded_by_instance_id': str(target.id),
        'superseded_by_version_no': target.version_no,
    })


def withdraw_paper(db: Session, paper_id: UUID, *, expected_revision: int,
                   reason: str) -> PaperInstance:
    paper = _paper(db, paper_id, lock=True)
    if paper.status != 'published':
        raise PaperConflict('only a published paper can be withdrawn')
    if paper.edit_revision != expected_revision:
        raise PaperConflict('paper was changed; refresh before withdrawing')
    if paper.invalidated_at is not None:
        raise PaperConflict('paper is already invalidated')
    if _lock_and_check_graded_attempts(db, paper.id):
        raise PaperConflict(GRADED_WITHDRAW_BLOCK_REASON)
    clean_reason = reason.strip()
    if not clean_reason:
        raise PaperValidation('withdrawal reason is required')
    paper.invalidated_at = datetime.now(timezone.utc)
    paper.invalidation_type = 'withdrawn'
    paper.invalidation_reason = clean_reason
    paper.invalidated_by = 'demo_teacher'
    _event(paper, 'withdraw', {'reason': clean_reason})
    db.flush()
    refresh_student_profiles(db, paper.student_id)
    return paper


def republish_paper(db: Session, paper_id: UUID, *, expected_revision: int) -> PaperInstance:
    paper = _paper(db, paper_id, lock=True)
    if paper.status != 'published':
        raise PaperConflict('only a published paper can be republished')
    if paper.edit_revision != expected_revision:
        raise PaperConflict('paper was changed; refresh before republishing')
    if paper.invalidation_type != 'withdrawn' or paper.invalidated_at is None:
        raise PaperConflict('only a withdrawn paper can be republished')
    if paper.superseded_by_instance_id is not None:
        raise PaperConflict('a superseded paper cannot be republished')
    if any(item.question_snapshot.get("question_type") not in PROFILE_QUESTION_TYPES
           for item in paper_items(db, paper.id)):
        raise PaperValidation('日常作业仅支持选择、填空、判断题；含计算题的旧试卷不能重新发布')

    active = db.scalar(select(PaperInstance).where(
        PaperInstance.assignment_id == paper.assignment_id,
        PaperInstance.student_id == paper.student_id,
        PaperInstance.status == 'published',
        PaperInstance.invalidated_at.is_(None),
        PaperInstance.id != paper.id,
    ).with_for_update())
    if active is not None:
        raise PaperConflict('another published version is currently active')

    previous = {
        'withdrawn_at': paper.invalidated_at.isoformat(),
        'withdrawal_reason': paper.invalidation_reason,
        'withdrawn_by': paper.invalidated_by,
    }
    paper.invalidated_at = None
    paper.invalidation_type = None
    paper.invalidation_reason = None
    paper.invalidated_by = None
    _event(paper, 'republish', previous)
    db.flush()
    refresh_student_profiles(db, paper.student_id)
    return paper


def publish_paper(db: Session, paper_id: UUID, *, expected_revision: int,
                  acknowledge_shortfall: bool = False,
                  acknowledgement_reason: str | None = None) -> PaperInstance:
    paper = _paper(db, paper_id, lock=True)
    _draft(paper, expected_revision)
    items = paper_items(db, paper.id)
    if not items:
        raise PaperValidation("cannot publish an empty paper")
    if [item.order_no for item in items] != list(range(1, len(items) + 1)):
        raise PaperValidation("paper order must be contiguous")
    if len({item.question_id for item in items}) != len(items):
        raise PaperValidation("paper contains duplicate questions")
    if any(not item.question_snapshot or not item.answer_rule_snapshot or
           not item.knowledge_point_snapshot.get("id") for item in items):
        raise PaperValidation("paper contains incomplete snapshots")
    current_questions = load_questions(db, {item.question_id for item in items})
    for item in items:
        if item.question_snapshot.get('question_type') not in PROFILE_QUESTION_TYPES:
            raise PaperValidation('日常作业仅支持选择、填空、判断题，请替换计算题或解答题后再发布')
        if not automatic_pool_ready(current_questions[item.question_id]):
            raise PaperValidation('试卷包含已撤出自动组卷池或未完成审核的题目，请换题后再发布')
        try:
            normalize_literacy_label(item.question_snapshot.get("core_literacy"))
        except ValueError as exc:
            original_qid = item.question_snapshot.get("original_qid") or item.question_id
            raise PaperValidation(
                f"题目 {original_qid} 的核心素养标签无效：{exc}"
            ) from exc
    personalized_plan = (paper.recommendation_audit or {}).get("personalized")
    if personalized_plan is not None:
        expected_personalized_count = int(
            personalized_plan.get("requested_count", 3)
        )
        actual_personalized_count = sum(
            (item.selection_audit or {}).get("source_module") == "personalized"
            for item in items
        )
        if actual_personalized_count != expected_personalized_count:
            raise PaperValidation(
                "个性化部分必须保留完整 3 道题；普通题量不足确认不能绕过此规则"
            )
    requested = (paper.recommendation_audit or {}).get("requested_count", len(items))
    if len(items) < requested:
        if not acknowledge_shortfall or not (acknowledgement_reason or "").strip():
            raise PaperConflict("paper is shorter than requested; explicit acknowledgement is required")
        _event(paper, "acknowledge_shortfall", {
            "requested_count": requested, "actual_count": len(items),
            "reason": acknowledgement_reason.strip(),
        })
    else:
        _event(paper, "publish", {"item_count": len(items)})
    active = db.scalar(select(PaperInstance).where(
        PaperInstance.assignment_id == paper.assignment_id,
        PaperInstance.student_id == paper.student_id,
        PaperInstance.status == 'published',
        PaperInstance.invalidated_at.is_(None),
    ).with_for_update())
    if paper.supersedes_instance_id is not None:
        source = _paper(db, paper.supersedes_instance_id, lock=True)
        if active is not None and active.id != source.id:
            raise PaperConflict('another published version is currently active')
        _supersede_paper(source, paper)
        db.flush()
    elif active is not None:
        raise PaperConflict('an active published version already exists')

    paper.instance_code = _new_code(db)
    paper.published_at = datetime.now(timezone.utc)
    paper.status = "published"
    db.flush()
    if paper.supersedes_instance_id is not None:
        refresh_student_profiles(db, paper.student_id)
    return paper


def revise_paper(db: Session, published_id: UUID) -> PaperInstance:
    source = _paper(db, published_id, lock=True)
    if source.status != "published":
        raise PaperConflict("only a published paper can create a new version")
    existing = db.scalar(select(PaperInstance).where(
        PaperInstance.supersedes_instance_id == source.id
    ).order_by(PaperInstance.version_no.desc()))
    if existing is not None:
        if existing.status == "draft":
            return existing
        raise PaperConflict("paper version was already superseded")
    max_version = int(db.scalar(select(func.max(PaperInstance.version_no)).where(
        PaperInstance.assignment_id == source.assignment_id,
        PaperInstance.student_id == source.student_id,
    )) or source.version_no)
    target = PaperInstance(
        assignment_id=source.assignment_id, student_id=source.student_id,
        version_no=max_version + 1, status="draft", supersedes_instance_id=source.id,
        recommendation_audit=deepcopy(source.recommendation_audit), edit_history=[{
            "event_id": str(uuid4()), "type": "create_revision",
            "at": datetime.now(timezone.utc).isoformat(), "actor": "demo_teacher",
            "before_revision": 0, "after_revision": 1,
            "details": {"supersedes_instance_id": str(source.id)},
        }], edit_revision=1,
    )
    db.add(target)
    db.flush()
    for item in paper_items(db, source.id):
        db.add(copy_item(
            source=item,
            paper_id=target.id,
            knowledge_point_snapshot=published_knowledge_point_snapshot(
                db, item.question_id, item.knowledge_point_snapshot
            ),
        ))
    db.flush()
    return target


def by_code(db: Session, instance_code: str) -> PaperByCodeOut:
    code = instance_code.strip().upper()
    paper = db.scalar(select(PaperInstance).where(
        PaperInstance.instance_code == code, PaperInstance.status == "published"
    ))
    if paper is None:
        raise LookupError("published paper not found")
    student = db.get(Student, paper.student_id)
    assignment = db.get(Assignment, paper.assignment_id)
    if student is None or assignment is None or paper.published_at is None or paper.instance_code is None:
        raise PaperValidation("published paper ownership is incomplete")
    return PaperByCodeOut(
        id=paper.id, assignment_id=assignment.id, assignment_title=assignment.title,
        student_id=student.id, student_code=student.student_code, student_name=student.display_name,
        version_no=paper.version_no, status=paper.status, instance_code=paper.instance_code,
        item_count=len(paper_items(db, paper.id)), published_at=paper.published_at,
        is_active=paper.invalidated_at is None, invalidated_at=paper.invalidated_at,
        invalidation_type=paper.invalidation_type,
        invalidation_reason=paper.invalidation_reason,
        superseded_by_instance_id=paper.superseded_by_instance_id,
    )
