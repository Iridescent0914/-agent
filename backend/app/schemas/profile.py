from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field


class EvidenceRef(BaseModel):
    attempt_id: UUID
    revision_no: int


class ExcludedEvidence(EvidenceRef):
    reason: str


class KnowledgePointOut(BaseModel):
    id: UUID
    code: str
    name: str
    chapter_code: str
    chapter_name: str = ""
    grade: str | None = None
    section: str | None = None
    core_competencies: list[str]
    available_question_count: int
    required_available_question_count: int = 0


class MasteryOut(BaseModel):
    knowledge_point_id: UUID
    knowledge_point_code: str
    knowledge_point_name: str
    assessment_status: Literal["unassessed", "insufficient", "assessed"]
    mastery: float | None
    learning_status: Literal["yes", "no", "weak"] | None = None
    confidence_level: Literal["low", "medium", "high"]
    attempt_count: int = 0
    correct_count: int = 0
    distinct_question_count: int = 0
    recent_attempt_count: int = 0
    recent_wrong_count: int = 0
    last_practiced_at: datetime | None = None
    first_practiced_at: datetime | None = None
    practiced_question_types: list[str] = Field(default_factory=list)
    practiced_difficulties: list[int] = Field(default_factory=list)
    available_question_count: int = 0


class LiteracyScoreOut(BaseModel):
    name: str
    score: int = Field(ge=0, le=100)
    score_source: Literal["baseline", "computed"]
    rule_version: str


class HistoryPointOut(EvidenceRef):
    answered_at: datetime
    question_id: UUID
    paper_instance_item_id: UUID
    is_correct: bool
    mastery_before: float
    mastery_after: float


class MasteryHistoryOut(BaseModel):
    mastery: MasteryOut
    points: list[HistoryPointOut]
    rule_version: str


class FocusPoint(BaseModel):
    knowledge_point_code: str
    knowledge_point_name: str
    category: Literal["practice", "recent_errors", "observe", "diagnostic"]
    reason: str
    evidence: list[EvidenceRef]


class ProfileSummary(BaseModel):
    total_attempt_count: int
    valid_attempt_count: int
    pending_review_count: int
    invalid_evidence_count: int
    distinct_question_count: int
    assessed_knowledge_point_count: int
    unassessed_knowledge_point_count: int


class HistoricalAccuracyOut(BaseModel):
    """All loaded history, one current confirmed result per Attempt (not blank)."""
    scope: Literal["all_history"] = "all_history"
    counting_unit: Literal["attempt_question_group"] = "attempt_question_group"
    confirmed_attempt_count: int = Field(default=0, ge=0)
    correct_count: int = Field(default=0, ge=0)
    incorrect_count: int = Field(default=0, ge=0)
    distinct_question_count: int = Field(default=0, ge=0)
    pending_review_count: int = Field(default=0, ge=0)
    excluded_attempt_count: int = Field(default=0, ge=0)
    accuracy: float | None = Field(default=None, ge=0, le=1)


class RecentWrong(EvidenceRef):
    answered_at: datetime
    original_qid: str
    knowledge_point_code: str
    knowledge_point_name: str


class ProfileOut(BaseModel):
    student_id: UUID
    computed_at: datetime
    rule_version: str
    summary: ProfileSummary
    historical_accuracy: HistoricalAccuracyOut = Field(default_factory=HistoricalAccuracyOut)
    masteries: list[MasteryOut]
    literacies: list[LiteracyScoreOut] = Field(default_factory=list)
    focus_points: list[FocusPoint]
    recent_errors: list[RecentWrong]
    excluded_evidence: list[ExcludedEvidence]
    evidence_note: str = "作答证据量按确认作答次数分档，并非统计置信概率；掌握度仅作教学参考。"


class AttemptEvidenceOut(BaseModel):
    id: UUID
    revision_no: int
    answered_at: datetime
    reviewed_at: datetime | None
    question_id: UUID
    paper_instance_item_id: UUID
    paper_instance_id: UUID | None
    instance_code: str | None
    question_snapshot: dict[str, Any]
    options_snapshot: Any | None
    answer_snapshot: Any | None
    answer_rule_snapshot: dict[str, Any]
    knowledge_point_snapshot: dict[str, Any]
    assets: list[dict[str, Any]]
    student_answer: Any | None
    grade_status: str
    is_correct: bool | None
    used_in_profile: bool
    exclusion_reason: str | None
    revision: dict[str, Any] | None
