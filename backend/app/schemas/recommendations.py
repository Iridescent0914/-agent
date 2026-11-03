from __future__ import annotations

from datetime import datetime
from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.profile import EvidenceRef


class RecommendationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chapter_code: str = Field(default="math.g6.rational", min_length=1, max_length=160)
    chapter_codes: list[str] = Field(default_factory=list, max_length=20)
    grade_codes: list[str] = Field(default_factory=list, max_length=20)
    new_knowledge_point_codes: list[str] = Field(default_factory=list, max_length=54)
    review_knowledge_point_codes: list[str] = Field(default_factory=list, max_length=54)
    new_count: int = Field(default=0, ge=0, le=30, strict=True)
    review_count: int = Field(default=0, ge=0, le=30, strict=True)
    question_types: list[Literal["choice", "fill_blank", "true_false"]] = Field(
        default_factory=lambda: ["choice", "fill_blank"], min_length=1, max_length=3)
    base_difficulty: int = Field(default=2, ge=1, le=5, strict=True)
    min_difficulty: int = Field(default=1, ge=1, le=5, strict=True)
    max_difficulty: int = Field(default=5, ge=1, le=5, strict=True)
    avoid_recent_days: int = Field(default=7, ge=0, le=3650, strict=True)
    max_knowledge_point_ratio: float = Field(default=0.5, gt=0, le=1, allow_inf_nan=False)

    @model_validator(mode="after")
    def validate_scope(self) -> Self:
        if not self.chapter_codes:
            self.chapter_codes = [self.chapter_code]
        if self.chapter_code not in self.chapter_codes:
            self.chapter_codes.insert(0, self.chapter_code)
        if len(set(self.chapter_codes)) != len(self.chapter_codes):
            raise ValueError("章节不能重复")
        if len(set(self.grade_codes)) != len(self.grade_codes):
            raise ValueError("年级不能重复")
        total = self.new_count + self.review_count
        if not 1 <= total <= 30:
            raise ValueError("总题量必须在 1 至 30 之间")
        if self.new_count and not self.new_knowledge_point_codes:
            raise ValueError("请指定新知识范围")
        if self.review_count and not self.review_knowledge_point_codes:
            raise ValueError("请指定回顾范围")
        for values in (self.new_knowledge_point_codes, self.review_knowledge_point_codes, self.question_types):
            if len(set(values)) != len(values):
                raise ValueError("范围或题型中不能有重复项")
        if set(self.new_knowledge_point_codes) & set(self.review_knowledge_point_codes):
            raise ValueError("新知识与回顾范围不能重叠")
        if not self.min_difficulty <= self.base_difficulty <= self.max_difficulty:
            raise ValueError("基础难度必须位于允许难度范围内")
        return self


class ScoreParts(BaseModel):
    weakness: float
    recent_wrong: float
    difficulty_match: float
    forgetting: float


class PersonalizedItemAudit(BaseModel):
    slot_no: int = Field(ge=1, le=3)
    literacy_name: str
    literacy_score: int = Field(ge=0, le=100)
    score_source: Literal["computed"] = "computed"
    rule_version: str
    base_star: int = Field(ge=1, le=5)
    target_star: int = Field(ge=1, le=5)
    target_stars: list[int] = Field(min_length=3, max_length=3)
    generated_at: datetime


class RecommendedQuestion(BaseModel):
    question_id: UUID
    original_qid: str
    question_type: str
    difficulty: int
    knowledge_point_code: str
    knowledge_point_name: str
    bucket: Literal["new", "review"]
    strategy: Literal["diagnostic", "personalized"]
    score: float
    components: ScoreParts
    contributions: ScoreParts
    target_difficulty: int
    reason_codes: list[str]
    reason_text: str
    evidence: list[EvidenceRef]
    source_module: Literal[
        "required", "personalized", "history", "weekly_summary", "legacy_recommendation"
    ] = (
        "legacy_recommendation"
    )
    personalized_audit: PersonalizedItemAudit | None = None


class FilterStage(BaseModel):
    code: str
    remaining: int
    removed: int


class BucketDiagnostic(BaseModel):
    bucket: Literal["new", "review"]
    knowledge_point_codes: list[str]
    requested_count: int
    selected_count: int
    shortfall: int
    stages: list[FilterStage]
    reason_codes: list[str]
    capped_knowledge_point_codes: list[str]


class PersonalizedDifficultyDiagnosticOut(BaseModel):
    difficulty: int = Field(ge=1, le=5)
    requested_count: int
    exact_candidate_count: int
    ready_candidate_count: int
    available_candidate_count: int
    selected_count: int
    shortfall: int
    reason_codes: list[str]


class PersonalizedSelectionPreviewOut(BaseModel):
    literacy_name: str
    literacy_score: int = Field(ge=0, le=100)
    score_source: Literal["computed"] = "computed"
    rule_version: str
    base_star: int = Field(ge=1, le=5)
    target_stars: list[int] = Field(min_length=3, max_length=3)
    requested_count: int = 3
    selected_count: int
    shortfall: int
    status: Literal["ready", "insufficient_candidates"]
    diagnostics: list[PersonalizedDifficultyDiagnosticOut]


class RecommendationOut(BaseModel):
    student_id: UUID
    as_of: datetime
    rule_version: str
    parameters: RecommendationRequest
    weights: ScoreParts
    status: Literal["ready", "insufficient_candidates"]
    requested_count: int
    selected_count: int
    shortfall: int
    new_shortfall: int
    review_shortfall: int
    personalized_shortfall: int = 0
    max_per_knowledge_point: int
    selected_per_knowledge_point: dict[str, int]
    diagnostics: list[BucketDiagnostic]
    items: list[RecommendedQuestion]
