from __future__ import annotations

from datetime import datetime
from math import floor
from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.profile import EvidenceRef


class RecommendationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chapter_code: str = Field(default="math.g6.rational", min_length=1, max_length=160)
    new_exam_point_codes: list[str] = Field(default_factory=list, max_length=54)
    review_exam_point_codes: list[str] = Field(default_factory=list, max_length=54)
    new_count: int = Field(default=0, ge=0, le=30, strict=True)
    review_count: int = Field(default=0, ge=0, le=30, strict=True)
    question_types: list[Literal["choice", "fill_blank"]] = Field(
        default_factory=lambda: ["choice", "fill_blank"], min_length=1, max_length=2)
    base_difficulty: int = Field(default=2, ge=1, le=5, strict=True)
    min_difficulty: int = Field(default=1, ge=1, le=5, strict=True)
    max_difficulty: int = Field(default=5, ge=1, le=5, strict=True)
    avoid_recent_days: int = Field(default=7, ge=0, le=3650, strict=True)
    max_exam_point_ratio: float = Field(default=0.5, gt=0, le=1, allow_inf_nan=False)

    @model_validator(mode="after")
    def validate_scope(self) -> Self:
        total = self.new_count + self.review_count
        if not 1 <= total <= 30:
            raise ValueError("总题量必须在 1 至 30 之间")
        if self.new_count and not self.new_exam_point_codes:
            raise ValueError("请指定新知识范围")
        if self.review_count and not self.review_exam_point_codes:
            raise ValueError("请指定回顾范围")
        for values in (self.new_exam_point_codes, self.review_exam_point_codes, self.question_types):
            if len(set(values)) != len(values):
                raise ValueError("范围或题型中不能有重复项")
        if set(self.new_exam_point_codes) & set(self.review_exam_point_codes):
            raise ValueError("新知识与回顾范围不能重叠")
        if not self.min_difficulty <= self.base_difficulty <= self.max_difficulty:
            raise ValueError("基础难度必须位于允许难度范围内")
        if floor(total * self.max_exam_point_ratio) < 1:
            raise ValueError("单考点允许题数不足 1，请提高考点占比")
        return self


class ScoreParts(BaseModel):
    weakness: float
    recent_wrong: float
    difficulty_match: float
    forgetting: float


class RecommendedQuestion(BaseModel):
    question_id: UUID
    original_qid: str
    question_type: str
    difficulty: int
    exam_point_code: str
    exam_point_name: str
    bucket: Literal["new", "review"]
    strategy: Literal["diagnostic", "personalized"]
    score: float
    components: ScoreParts
    contributions: ScoreParts
    target_difficulty: int
    reason_codes: list[str]
    reason_text: str
    evidence: list[EvidenceRef]


class FilterStage(BaseModel):
    code: str
    remaining: int
    removed: int


class BucketDiagnostic(BaseModel):
    bucket: Literal["new", "review"]
    exam_point_codes: list[str]
    requested_count: int
    selected_count: int
    shortfall: int
    stages: list[FilterStage]
    reason_codes: list[str]
    capped_exam_point_codes: list[str]


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
    max_per_exam_point: int
    selected_per_exam_point: dict[str, int]
    diagnostics: list[BucketDiagnostic]
    items: list[RecommendedQuestion]
