from __future__ import annotations

from collections import Counter
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import secrets
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    Assignment, ExamPoint, PaperInstance, PaperInstanceItem, Question,
    QuestionExamPoint, Student,
)
from app.schemas.assignments import AssignmentDetailOut, AssignmentListItem
from app.schemas.papers import (
    AlternativeOut, PaperByCodeOut, PaperInstanceStudentOut,
    PaperInstanceTeacherOut, PaperItemOut, PaperSummaryOut, StudentPaperItemOut,
)
from app.schemas.recommendations import RecommendationRequest
from app.services.assets import resolve_assets
from app.services.paper_snapshots import copy_item, load_question, snapshot_payload
from app.services.recommendations import Candidate, score_candidate
from app.services.student_profile import load_context


class PaperConflict(RuntimeError):
    pass


class PaperValidation(ValueError):
    pass


def paper_items(db: Session, paper_id: UUID) -> list[PaperInstanceItem]:
    return list(db.scalars(
        select(PaperInstanceItem)
        .where(PaperInstanceItem.paper_instance_id == paper_id)
        .order_by(PaperInstanceItem.order_no, PaperInstanceItem.id)
    ))


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


def _summary(db: Session, paper: PaperInstance, student: Student | None = None,
             item_count: int | None = None) -> PaperSummaryOut:
    student = student or db.get(Student, paper.student_id)
    if student is None:
        raise LookupError("paper student not found")
    count = item_count if item_count is not None else int(db.scalar(
        select(func.count(PaperInstanceItem.id)).where(PaperInstanceItem.paper_instance_id == paper.id)
    ) or 0)
    audit = paper.recommendation_audit or {}
    return PaperSummaryOut(
        id=paper.id, assignment_id=paper.assignment_id, student_id=paper.student_id,
        student_code=student.student_code, student_name=student.display_name,
        version_no=paper.version_no, status=paper.status, item_count=count,
        requested_count=audit.get("requested_count"), shortfall=audit.get("shortfall"),
        edit_revision=paper.edit_revision, instance_code=paper.instance_code,
        supersedes_instance_id=paper.supersedes_instance_id,
        superseded_by_instance_id=paper.superseded_by_instance_id,
        is_active=paper.status == 'published' and paper.invalidated_at is None,
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
        output.append(PaperItemOut(
            id=item.id, question_id=item.question_id, order_no=item.order_no,
            question_snapshot=item.question_snapshot, options_snapshot=item.options_snapshot,
            answer_snapshot=item.answer_snapshot, answer_rule_snapshot=item.answer_rule_snapshot,
            exam_point_snapshot=item.exam_point_snapshot,
            assets=resolve_assets(item.question_snapshot.get("assets", [])),
            selection_audit=item.selection_audit,
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
    items = [StudentPaperItemOut(
        id=item.id, order_no=item.order_no, question_snapshot=item.question_snapshot,
        options_snapshot=item.options_snapshot, exam_point_snapshot=item.exam_point_snapshot,
        assets=resolve_assets(item.question_snapshot.get("assets", [])),
    ) for item in paper_items(db, paper.id)]
    return PaperInstanceStudentOut(
        id=paper.id, assignment_id=paper.assignment_id, student_id=paper.student_id,
        student_code=student.student_code, version_no=paper.version_no,
        status=paper.status, instance_code=paper.instance_code,
        is_active=paper.status == 'published' and paper.invalidated_at is None,
        invalidated_at=paper.invalidated_at, invalidation_type=paper.invalidation_type,
        invalidation_reason=paper.invalidation_reason,
        superseded_by_instance_id=paper.superseded_by_instance_id, items=items,
    )


def assignment_detail(db: Session, assignment_id: UUID) -> AssignmentDetailOut:
    assignment = db.get(Assignment, assignment_id)
    if assignment is None:
        raise LookupError("assignment not found")
    papers = list(db.scalars(
        select(PaperInstance).where(PaperInstance.assignment_id == assignment.id)
        .order_by(PaperInstance.student_id, PaperInstance.version_no)
    ))
    summaries = [_summary(db, paper) for paper in papers]
    return AssignmentDetailOut(
        id=assignment.id, title=assignment.title, chapter_code=assignment.chapter_code,
        assignment_type=assignment.assignment_type,
        student_count=len({paper.student_id for paper in papers}),
        draft_count=sum(paper.status == "draft" for paper in papers),
        published_count=sum(
            paper.status == 'published' and paper.invalidated_at is None for paper in papers
        ),
        invalidated_count=sum(paper.invalidated_at is not None for paper in papers),
        created_at=assignment.created_at, teaching_scope=assignment.teaching_scope,
        generation_config=assignment.generation_config, papers=summaries,
    )


def list_assignment_items(db: Session, *, offset: int, limit: int) -> tuple[list[AssignmentListItem], int]:
    total = int(db.scalar(select(func.count(Assignment.id)).where(Assignment.assignment_type == "practice")) or 0)
    assignments = list(db.scalars(
        select(Assignment).where(Assignment.assignment_type == "practice")
        .order_by(Assignment.created_at.desc(), Assignment.id).offset(offset).limit(limit)
    ))
    result = []
    for assignment in assignments:
        papers = list(db.scalars(select(PaperInstance).where(PaperInstance.assignment_id == assignment.id)))
        result.append(AssignmentListItem(
            id=assignment.id, title=assignment.title, chapter_code=assignment.chapter_code,
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


def _parameters(db: Session, paper: PaperInstance) -> tuple[RecommendationRequest, datetime]:
    assignment = db.get(Assignment, paper.assignment_id)
    config = (assignment.generation_config if assignment else None) or {}
    raw = config.get("recommendation_parameters")
    as_of = config.get("as_of")
    if not raw or not as_of:
        raise PaperValidation("paper has no M3 generation configuration")
    return RecommendationRequest.model_validate(raw), datetime.fromisoformat(as_of)


def alternatives(db: Session, paper_id: UUID, item_id: UUID) -> AlternativeOut:
    paper = _paper(db, paper_id)
    if paper.status != "draft":
        raise PaperConflict("published paper is immutable")
    items = paper_items(db, paper.id)
    target = next((item for item in items if item.id == item_id), None)
    if target is None:
        raise LookupError("paper item not found")
    bucket = (target.selection_audit or {}).get("bucket")
    if bucket not in ("new", "review"):
        raise PaperValidation("paper item has no recommendation bucket")
    params, as_of = _parameters(db, paper)
    context = load_context(db, paper.student_id)
    allowed_codes = params.new_exam_point_codes if bucket == "new" else params.review_exam_point_codes
    existing_ids = {item.question_id for item in items}
    per_exam = Counter(
        (item.exam_point_snapshot.get("main") or {}).get("id")
        for item in items if item.id != target.id
    )
    practiced = {record.attempt.question_id for record in context.records
                 if params.avoid_recent_days and
                 as_of - timedelta(days=params.avoid_recent_days) <= record.attempt.answered_at <= as_of}
    rows = db.execute(
        select(Question, ExamPoint)
        .join(QuestionExamPoint, QuestionExamPoint.question_id == Question.id)
        .join(ExamPoint, ExamPoint.id == QuestionExamPoint.exam_point_id)
        .where(
            Question.chapter_code == params.chapter_code,
            Question.archived_at.is_(None),
            QuestionExamPoint.relation_type == 'main',
        )
    ).all()
    ranked = []
    cap = max(1, int((params.new_count + params.review_count) * params.max_exam_point_ratio))
    for question, exam in rows:
        if question.id in existing_ids or question.id in practiced:
            continue
        if exam.code not in allowed_codes or question.question_type not in params.question_types:
            continue
        if not params.min_difficulty <= question.difficulty <= params.max_difficulty:
            continue
        if question.grading_readiness != "machine_ready" or question.source_review_status != "reviewed":
            continue
        if per_exam[exam.code] >= cap:
            continue
        candidate = Candidate(
            question.id, question.original_qid, question.question_type, question.difficulty,
            exam.id, exam.code, exam.name,
        )
        ranked.append(score_candidate(
            candidate, context.results.get((paper.student_id, exam.id)), params, bucket, as_of
        ))
    ranked.sort(key=lambda item: (-item.score, item.original_qid, item.question_id.int))
    return AlternativeOut(items=ranked[:20])


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
    old = {"item_id": str(item.id), "question_id": str(item.question_id),
           "original_qid": item.question_snapshot.get("original_qid")}
    payload = snapshot_payload(load_question(db, replacement_id))
    item.question_id = replacement_id
    item.question_snapshot = payload["question_snapshot"]
    item.options_snapshot = payload["options_snapshot"]
    item.answer_snapshot = payload["answer_snapshot"]
    item.answer_rule_snapshot = payload["answer_rule_snapshot"]
    item.exam_point_snapshot = payload["exam_point_snapshot"]
    item.answer_region = None
    item.selection_audit = {
        "schema_version": "m3-selection-audit-v1", "source": "teacher_replacement",
        "selected_at": datetime.now(timezone.utc).isoformat(), "replaces": old,
        **choice.model_dump(mode="json"),
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
    clean_reason = reason.strip()
    if not clean_reason:
        raise PaperValidation('withdrawal reason is required')
    paper.invalidated_at = datetime.now(timezone.utc)
    paper.invalidation_type = 'withdrawn'
    paper.invalidation_reason = clean_reason
    paper.invalidated_by = 'demo_teacher'
    _event(paper, 'withdraw', {'reason': clean_reason})
    db.flush()
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
           not item.exam_point_snapshot.get("main") for item in items):
        raise PaperValidation("paper contains incomplete snapshots")
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
        db.add(copy_item(source=item, paper_id=target.id))
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
