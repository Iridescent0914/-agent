"""Descriptive comparison only: no thresholds, provider calls or persisted results."""
from collections import Counter
from dataclasses import replace
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import PaperInstance, PaperInstanceItem
from app.schemas.paper_accuracy import (
    AccuracySampleOut, KnowledgeHistoryCoverageOut, KnowledgeHistoryOut,
    PaperAccuracyComparisonOut, PaperAccuracySampleOut,
)
from app.services.profile_evidence import snapshot_knowledge_code
from app.services.student_profile import ProfileContext, historical_accuracy, load_context


def build_accuracy_comparison(
    context: ProfileContext, paper: PaperInstance, items: list[PaperInstanceItem],
) -> PaperAccuracyComparisonOut:
    current = [r for r in context.records if r.item is not None and r.item.paper_instance_id == paper.id]
    # Even pending / invalid evidence from this paper must never enter its baseline.
    history = [r for r in context.records if r.item is None or r.item.paper_instance_id != paper.id]
    current_stats = historical_accuracy(replace(context, records=current))
    history_stats = historical_accuracy(replace(context, records=history))
    total = len(items)
    submitted_ids = {r.attempt.paper_instance_item_id for r in current}
    active = paper.status == "published" and paper.invalidated_at is None
    count = current_stats.confirmed_attempt_count
    if not active:
        status = "inactive_paper"
    elif not count:
        status = "no_paper_results"
    elif not history_stats.confirmed_attempt_count:
        status = "no_history"
    elif count < total:
        status = "incomplete_paper"
    else:
        status = "available"

    known_codes = {point.code for point in context.catalog}
    history_counts = Counter(
        snapshot_knowledge_code(r.item) for r in history
        if r.problem in {None, "unsupported_profile_question_type"}
    )
    by_code: dict[str | None, KnowledgeHistoryOut] = {}
    for item in items:
        code = snapshot_knowledge_code(item)
        mapped = code in known_codes
        key = code if mapped else None
        if key not in by_code:
            snapshot = item.knowledge_point_snapshot or {}
            by_code[key] = KnowledgeHistoryOut(
                knowledge_point_code=key,
                knowledge_point_name=(snapshot.get("name") or code) if mapped else "知识点未映射",
                paper_question_count=0,
                historical_confirmed_attempt_count=history_counts[code] if mapped else 0,
                history_status="unmapped" if not mapped else "seen" if history_counts[code] else "unseen",
            )
        by_code[key].paper_question_count += 1
    points = list(by_code.values())
    return PaperAccuracyComparisonOut(
        paper_instance_id=paper.id, computed_at=datetime.now(timezone.utc), data_status=status,
        paper=PaperAccuracySampleOut(
            **current_stats.model_dump(exclude={"scope", "counting_unit"}),
            total_question_count=total,
            unsubmitted_question_count=sum(item.id not in submitted_ids for item in items),
            judged_coverage=count / total if total else None,
        ),
        history=AccuracySampleOut.model_validate(history_stats.model_dump()),
        knowledge_coverage=KnowledgeHistoryCoverageOut(
            seen_question_count=sum(p.paper_question_count for p in points if p.history_status == "seen"),
            unseen_question_count=sum(p.paper_question_count for p in points if p.history_status == "unseen"),
            unmapped_question_count=sum(p.paper_question_count for p in points if p.history_status == "unmapped"),
            points=points,
        ),
    )


def paper_accuracy_comparison(db: Session, paper_id: UUID) -> PaperAccuracyComparisonOut:
    paper = db.get(PaperInstance, paper_id)
    if paper is None:
        raise LookupError("paper instance not found")
    context = load_context(db, paper.student_id, include_literacy=False)
    items = list(db.scalars(select(PaperInstanceItem).where(
        PaperInstanceItem.paper_instance_id == paper.id,
    ).order_by(PaperInstanceItem.order_no, PaperInstanceItem.id)))
    return build_accuracy_comparison(context, paper, items)
