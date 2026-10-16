from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from math import floor
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ExamPoint, Question, QuestionExamPoint
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


@dataclass(frozen=True)
class Candidate:
    question_id: UUID
    original_qid: str
    question_type: str
    difficulty: int
    exam_point_id: UUID
    exam_point_code: str
    exam_point_name: str
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
        exam_point_code=candidate.exam_point_code, exam_point_name=candidate.exam_point_name,
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
    cap = floor(total * params.max_exam_point_ratio)
    excluded = {qid for qid, at in practiced
                if params.avoid_recent_days and as_of - timedelta(days=params.avoid_recent_days) <= at <= as_of}
    selected = []
    seen: set[UUID] = set()
    per_exam: Counter[str] = Counter()
    diagnostics = []
    for bucket, codes, requested in (("new", params.new_exam_point_codes, params.new_count),
                                      ("review", params.review_exam_point_codes, params.review_count)):
        pool = list({candidate.question_id: candidate for candidate in candidates}.values())
        stages = [FilterStage(code="chapter", remaining=len(pool), removed=0)]
        filters = [
            ("grading_readiness", lambda c: c.ready),
            ("no_questions_in_scope", lambda c: c.exam_point_code in codes),
            ("question_type_filter", lambda c: c.question_type in params.question_types),
            ("difficulty_filter", lambda c: params.min_difficulty <= c.difficulty <= params.max_difficulty),
            ("recently_practiced", lambda c: c.question_id not in excluded),
        ]
        for code, predicate in filters:
            before = len(pool)
            pool = [candidate for candidate in pool if predicate(candidate)]
            stages.append(FilterStage(code=code, remaining=len(pool), removed=before - len(pool)))
        ranked = [score_candidate(c, results.get((student_id, c.exam_point_id)), params, bucket, as_of) for c in pool]
        ranked.sort(key=lambda item: (-item.score, item.original_qid, item.question_id.int))
        bucket_selected = []
        capped = set()
        for item in ranked:
            if len(bucket_selected) == requested:
                break
            if item.question_id in seen:
                continue
            if per_exam[item.exam_point_code] >= cap:
                capped.add(item.exam_point_code)
                continue
            bucket_selected.append(item)
            seen.add(item.question_id)
            per_exam[item.exam_point_code] += 1
        selected.extend(bucket_selected)
        shortfall = requested - len(bucket_selected)
        reasons = []
        if shortfall:
            if not any(candidate.exam_point_code in codes for candidate in candidates):
                reasons.append("no_questions_in_scope")
            reasons.extend(stage.code for stage in stages
                           if stage.removed and stage.code not in ("chapter", "no_questions_in_scope"))
            if capped:
                reasons.append("exam_point_cap")
            if not reasons:
                reasons.append("insufficient_scope_inventory")
        diagnostics.append(BucketDiagnostic(
            bucket=bucket, exam_point_codes=codes, requested_count=requested,
            selected_count=len(bucket_selected), shortfall=shortfall, stages=stages,
            reason_codes=reasons, capped_exam_point_codes=sorted(capped),
        ))
    return RecommendationOut(
        student_id=student_id, as_of=as_of, rule_version=RULE_VERSION, parameters=params,
        weights=ScoreParts(**WEIGHTS), status="ready" if len(selected) == total else "insufficient_candidates",
        requested_count=total, selected_count=len(selected), shortfall=total - len(selected),
        new_shortfall=diagnostics[0].shortfall, review_shortfall=diagnostics[1].shortfall,
        max_per_exam_point=cap, selected_per_exam_point=dict(per_exam), diagnostics=diagnostics, items=selected,
    )


def recommendation_candidates(db: Session, chapter_code: str) -> list[Candidate]:
    rows = db.execute(
        select(Question, ExamPoint)
        .join(QuestionExamPoint, QuestionExamPoint.question_id == Question.id)
        .join(ExamPoint, ExamPoint.id == QuestionExamPoint.exam_point_id)
        .where(
            Question.chapter_code == chapter_code,
            Question.archived_at.is_(None),
            QuestionExamPoint.relation_type == 'main',
        )
    ).all()
    return [Candidate(
        question.id, question.original_qid, question.question_type,
        question.difficulty, exam.id, exam.code, exam.name,
        question.grading_readiness == "machine_ready"
        and question.source_review_status == "reviewed",
    ) for question, exam in rows]


def preview(db: Session, context: ProfileContext, params: RecommendationRequest,
            *, as_of: datetime,
            candidates: list[Candidate] | None = None) -> RecommendationOut:
    allowed = {exam.code for exam in context.catalog if exam.chapter_code == params.chapter_code}
    if not set(params.new_exam_point_codes + params.review_exam_point_codes) <= allowed:
        raise ValueError("知识点不存在或不属于当前章节")
    if any(record.problem not in (None, "needs_review") for record in context.records):
        raise ValueError("存在异常作答证据，请修复后再预览推荐")
    if candidates is None:
        candidates = recommendation_candidates(db, params.chapter_code)
    return select_recommendations(student_id=context.student.id, candidates=candidates, results=context.results,
                                  practiced=[(r.attempt.question_id, r.attempt.answered_at) for r in context.records],
                                  params=params, as_of=as_of)
