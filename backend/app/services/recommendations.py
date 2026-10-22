from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from math import floor
import re
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import KnowledgePoint, Question
from app.schemas.profile import EvidenceRef
from app.schemas.recommendations import (
    BucketDiagnostic, FilterStage, RecommendationOut, RecommendationRequest,
    RecommendedQuestion, ScoreParts,
)
from app.services.mastery import MasteryResult, RULE_VERSION, quantize
from app.services.student_profile import ProfileContext


WEIGHTS = {"weakness": Decimal("0.40"), "recent_wrong": Decimal("0.25"),
           "difficulty_match": Decimal("0.20"), "forgetting": Decimal("0.15")}
REVIEW_INTERVAL_DAYS = Decimal(30)


def _core_literacy_tokens(value: str | None) -> set[str]:
    return {
        token.strip()
        for token in re.split(r"[、,，;；/|]+", value or "")
        if token.strip()
    }


@dataclass(frozen=True)
class Candidate:
    question_id: UUID
    original_qid: str
    question_type: str
    difficulty: int
    knowledge_point_id: UUID
    knowledge_point_code: str
    knowledge_point_name: str
    ready: bool = True


def score_candidate(candidate: Candidate, result: MasteryResult | None,
                    params: RecommendationRequest, bucket: str, as_of: datetime) -> RecommendedQuestion:
    personalized = result is not None and result.attempt_count >= 3 and result.distinct_question_count >= 2
    target = params.base_difficulty
    parts = {name: Decimal(0) for name in WEIGHTS}
    reasons = ["teacher_scope"]
    refs = []
    if personalized:
        target = 2 if result.mastery < Decimal("0.4") else 3 if result.mastery <= Decimal("0.7") else 4
        target = max(params.min_difficulty, min(params.max_difficulty, target))
        parts["weakness"] = 1 - result.mastery
        parts["recent_wrong"] = Decimal(result.recent_wrong_count) / result.recent_attempt_count
        if bucket == "review":
            days = Decimal(str((as_of - result.last_practiced_at).total_seconds())) / Decimal(86400)
            parts["forgetting"] = min(max(days, Decimal(0)) / REVIEW_INTERVAL_DAYS, Decimal(1))
            reasons.append("review_interval")
        reasons.extend(["mastery_reference", "recent_results"])
        refs = [EvidenceRef(attempt_id=point.attempt_id, revision_no=point.revision_no) for point in result.history]
        text = f"该知识点最近 {result.recent_attempt_count} 次确认作答错 {result.recent_wrong_count} 次；"
    else:
        reasons.append("insufficient_evidence")
        text = "证据不足，按教师基础难度安排诊断练习；"
    parts["difficulty_match"] = max(Decimal(0), 1 - Decimal(abs(candidate.difficulty - target)) / 4)
    contributions = {name: quantize(value * WEIGHTS[name]) for name, value in parts.items()}
    reasons.append("difficulty_match")
    return RecommendedQuestion(
        question_id=candidate.question_id, original_qid=candidate.original_qid,
        question_type=candidate.question_type, difficulty=candidate.difficulty,
        knowledge_point_code=candidate.knowledge_point_code,
        knowledge_point_name=candidate.knowledge_point_name,
        bucket=bucket, strategy="personalized" if personalized else "diagnostic",
        score=float(sum(contributions.values())), components=ScoreParts(**{k: float(v) for k, v in parts.items()}),
        contributions=ScoreParts(**contributions), target_difficulty=target,
        reason_codes=reasons, reason_text=text + f"本题难度 {candidate.difficulty}，目标难度 {target}。",
        evidence=refs,
    )


def select_recommendations(*, student_id: UUID, candidates: list[Candidate],
                           results: dict[tuple[UUID, UUID], MasteryResult],
                           practiced: list[tuple[UUID, datetime]], params: RecommendationRequest,
                           as_of: datetime) -> RecommendationOut:
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("as_of must include a timezone")
    total = params.new_count + params.review_count
    cap = floor(total * params.max_knowledge_point_ratio)
    excluded = {qid for qid, at in practiced
                if params.avoid_recent_days and as_of - timedelta(days=params.avoid_recent_days) <= at <= as_of}
    selected = []
    seen: set[UUID] = set()
    per_knowledge: Counter[str] = Counter()
    diagnostics = []
    for bucket, codes, requested in (
        ("new", params.new_knowledge_point_codes, params.new_count),
        ("review", params.review_knowledge_point_codes, params.review_count),
    ):
        pool = list({candidate.question_id: candidate for candidate in candidates}.values())
        stages = [FilterStage(code="chapter", remaining=len(pool), removed=0)]
        filters = [
            ("grading_readiness", lambda c: c.ready),
            ("no_questions_in_scope", lambda c: c.knowledge_point_code in codes),
            ("question_type_filter", lambda c: c.question_type in params.question_types),
            ("difficulty_filter", lambda c: params.min_difficulty <= c.difficulty <= params.max_difficulty),
            ("recently_practiced", lambda c: c.question_id not in excluded),
        ]
        for code, predicate in filters:
            before = len(pool)
            pool = [candidate for candidate in pool if predicate(candidate)]
            stages.append(FilterStage(code=code, remaining=len(pool), removed=before - len(pool)))
        ranked = [
            score_candidate(
                candidate,
                results.get((student_id, candidate.knowledge_point_id)),
                params,
                bucket,
                as_of,
            )
            for candidate in pool
        ]
        ranked.sort(key=lambda item: (-item.score, item.original_qid, item.question_id.int))
        bucket_selected = []
        capped = set()
        for item in ranked:
            if len(bucket_selected) == requested:
                break
            if item.question_id in seen:
                continue
            if per_knowledge[item.knowledge_point_code] >= cap:
                capped.add(item.knowledge_point_code)
                continue
            bucket_selected.append(item)
            seen.add(item.question_id)
            per_knowledge[item.knowledge_point_code] += 1
        selected.extend(bucket_selected)
        shortfall = requested - len(bucket_selected)
        reasons = []
        if shortfall:
            if not any(candidate.knowledge_point_code in codes for candidate in candidates):
                reasons.append("no_questions_in_scope")
            reasons.extend(stage.code for stage in stages
                           if stage.removed and stage.code not in ("chapter", "no_questions_in_scope"))
            if capped:
                reasons.append("knowledge_point_cap")
            if not reasons:
                reasons.append("insufficient_scope_inventory")
        diagnostics.append(BucketDiagnostic(
            bucket=bucket, knowledge_point_codes=codes, requested_count=requested,
            selected_count=len(bucket_selected), shortfall=shortfall, stages=stages,
            reason_codes=reasons, capped_knowledge_point_codes=sorted(capped),
        ))
    return RecommendationOut(
        student_id=student_id, as_of=as_of, rule_version=RULE_VERSION, parameters=params,
        weights=ScoreParts(**WEIGHTS), status="ready" if len(selected) == total else "insufficient_candidates",
        requested_count=total, selected_count=len(selected), shortfall=total - len(selected),
        new_shortfall=diagnostics[0].shortfall, review_shortfall=diagnostics[1].shortfall,
        max_per_knowledge_point=cap,
        selected_per_knowledge_point=dict(per_knowledge),
        diagnostics=diagnostics,
        items=selected,
    )


def recommendation_candidates(
    db: Session, chapter_code: str | list[str], grades: list[str] | None = None
) -> list[Candidate]:
    chapter_codes = [chapter_code] if isinstance(chapter_code, str) else chapter_code
    filters = [
        Question.chapter_code.in_(chapter_codes),
    ]
    if grades:
        filters.append(Question.grade.in_(grades))
    rows = db.execute(
        select(Question, KnowledgePoint)
        .join(KnowledgePoint, KnowledgePoint.id == Question.main_knowledge_point_id)
        .where(
            *filters,
            Question.archived_at.is_(None),
        )
    ).all()
    return [Candidate(
        question.id, question.original_qid, question.question_type,
        question.difficulty,
        knowledge_point.id,
        knowledge_point.code,
        knowledge_point.name,
        question.grading_readiness == "machine_ready"
        and question.source_review_status == "reviewed",
    ) for question, knowledge_point in rows]


def required_question_candidates(
    db: Session,
    chapter_codes: list[str],
    grades: list[str],
    knowledge_point_codes: list[str] | None = None,
    core_literacies: list[str] | None = None,
) -> list[Candidate]:
    """Load the shared, teacher-scoped pool used by mandatory questions.

    Mandatory questions are selected once per assignment, not once per student.
    Knowledge-point and core-literacy filters are independent. When both are
    provided, a question must match both scopes (their intersection).
    """

    filters = [
        Question.chapter_code.in_(chapter_codes),
        Question.grade.in_(grades),
        Question.archived_at.is_(None),
    ]
    rows = db.execute(
        select(Question, KnowledgePoint)
        .join(KnowledgePoint, KnowledgePoint.id == Question.main_knowledge_point_id)
        .where(*filters)
    ).all()
    knowledge_scope = set(knowledge_point_codes or [])
    literacy_scope = set(core_literacies or [])
    candidates = []
    for question, knowledge_point in rows:
        if knowledge_scope and knowledge_point.code not in knowledge_scope:
            continue
        if literacy_scope and not literacy_scope.intersection(
            _core_literacy_tokens(question.core_literacy)
        ):
            continue
        candidates.append(Candidate(
            question.id, question.original_qid, question.question_type,
            question.difficulty, knowledge_point.id, knowledge_point.code,
            knowledge_point.name,
            question.grading_readiness == "machine_ready"
            and question.source_review_status == "reviewed",
        ))
    return candidates


def select_required_recommendations(
    *, student_id: UUID, candidates: list[Candidate],
    knowledge_point_codes: list[str], difficulty_counts: dict[str, int],
    params: RecommendationRequest, as_of: datetime,
) -> RecommendationOut:
    """Select one deterministic question set shared by every student."""

    unique_candidates = {
        candidate.question_id: candidate for candidate in candidates
        if candidate.ready
    }
    selected: list[RecommendedQuestion] = []
    diagnostics: list[BucketDiagnostic] = []
    selected_per_knowledge: Counter[str] = Counter()
    used: set[UUID] = set()
    for difficulty in sorted(difficulty_counts, key=int):
        requested = difficulty_counts[difficulty]
        pool = sorted(
            (candidate for candidate in unique_candidates.values()
             if candidate.difficulty == int(difficulty) and candidate.question_id not in used),
            key=lambda candidate: (candidate.original_qid, candidate.question_id.int),
        )
        chosen = pool[:requested]
        used.update(candidate.question_id for candidate in chosen)
        for candidate in chosen:
            item = score_candidate(candidate, None, params, "new", as_of)
            selected.append(item.model_copy(update={
                "reason_codes": ["required_question", f"difficulty_{difficulty}"],
                "reason_text": f"教师设置的必做题，难度 {difficulty}",
            }))
            selected_per_knowledge[candidate.knowledge_point_code] += 1
        shortfall = requested - len(chosen)
        diagnostics.append(BucketDiagnostic(
            bucket="new", knowledge_point_codes=knowledge_point_codes,
            requested_count=requested, selected_count=len(chosen),
            shortfall=shortfall,
            stages=[
                FilterStage(code="teacher_scope", remaining=len(unique_candidates), removed=0),
                FilterStage(code="difficulty_filter", remaining=len(pool), removed=0),
                FilterStage(code="grading_readiness", remaining=len(chosen), removed=len(pool) - len(chosen)),
            ],
            reason_codes=["insufficient_difficulty_inventory"] if shortfall else [],
            capped_knowledge_point_codes=[],
        ))
    total = sum(difficulty_counts.values())
    selected_count = len(selected)
    shortfall = total - selected_count
    diagnostics.append(BucketDiagnostic(
        bucket="review", knowledge_point_codes=[], requested_count=0,
        selected_count=0, shortfall=0, stages=[], reason_codes=[],
        capped_knowledge_point_codes=[],
    ))
    return RecommendationOut(
        student_id=student_id, as_of=as_of, rule_version=RULE_VERSION,
        parameters=params, weights=ScoreParts(**WEIGHTS),
        status="ready" if shortfall == 0 else "insufficient_candidates",
        requested_count=total, selected_count=selected_count, shortfall=shortfall,
        new_shortfall=shortfall, review_shortfall=0,
        max_per_knowledge_point=total,
        selected_per_knowledge_point=dict(selected_per_knowledge),
        diagnostics=diagnostics, items=selected,
    )


def preview(db: Session, context: ProfileContext, params: RecommendationRequest,
            *, as_of: datetime,
            candidates: list[Candidate] | None = None) -> RecommendationOut:
    chapter_codes = params.chapter_codes or [params.chapter_code]
    allowed = {
        knowledge_point.code
        for knowledge_point in context.catalog
        if knowledge_point.chapter_code in chapter_codes
    }
    if not set(
        params.new_knowledge_point_codes + params.review_knowledge_point_codes
    ) <= allowed:
        raise ValueError("知识点不存在或不属于当前章节")
    if any(record.problem not in (None, "needs_review") for record in context.records):
        raise ValueError("存在异常作答证据，请修复后再预览推荐")
    if candidates is None:
        candidates = recommendation_candidates(db, chapter_codes, params.grade_codes)
    return select_recommendations(student_id=context.student.id, candidates=candidates, results=context.results,
                                  practiced=[(r.attempt.question_id, r.attempt.answered_at) for r in context.records],
                                  params=params, as_of=as_of)
