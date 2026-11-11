from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.papers import PaperSummaryOut
from app.services.knowledge_catalog import formal_literacy_dimensions
from app.schemas.recommendations import (
    PersonalizedSelectionPreviewOut,
    RecommendationOut,
    RecommendationRequest,
)


class RequiredQuestionFilter(BaseModel):
    """Filters for the mandatory-question module."""

    model_config = ConfigDict(extra="forbid")

    knowledge_point_codes: list[str] = Field(default_factory=list, max_length=54)
    core_literacies: list[str] = Field(default_factory=list, max_length=32)
    difficulty_counts: dict[str, int] = Field(default_factory=dict)

    @field_validator("core_literacies")
    @classmethod
    def validate_core_literacies(cls, values: list[str]) -> list[str]:
        allowed = set(formal_literacy_dimensions())
        if any(value not in allowed for value in values):
            raise ValueError("核心素养必须使用正式知识图谱的九项名称")
        return list(dict.fromkeys(values))

    @model_validator(mode="after")
    def validate_difficulty_counts(self):
        invalid = set(self.difficulty_counts) - {"1", "2", "3", "4", "5"}
        if invalid:
            raise ValueError("difficulty_counts keys must be between 1 and 5")
        if any(not isinstance(value, int) or isinstance(value, bool) or value < 0 for value in self.difficulty_counts.values()):
            raise ValueError("difficulty_counts values must be non-negative integers")
        if sum(self.difficulty_counts.values()) > 30:
            raise ValueError("difficulty_counts total cannot exceed 30")
        return self


class PersonalizedQuestionFilter(BaseModel):
    model_config = ConfigDict(extra="forbid")

    knowledge_point_codes: list[str] = Field(default_factory=list, max_length=54)
    primary_competency: str | None = Field(default=None, max_length=120)
    question_count: int = Field(default=3, ge=3, le=3, strict=True)

    @field_validator("primary_competency")
    @classmethod
    def validate_primary_competency(cls, value: str | None) -> str | None:
        if value is not None and value not in formal_literacy_dimensions():
            raise ValueError("主要能力必须使用正式知识图谱的九项名称")
        return value

    @model_validator(mode="after")
    def require_scope_and_competency(self):
        if not self.knowledge_point_codes:
            raise ValueError("个性化模块必须选择至少一个知识点")
        if not self.primary_competency or not self.primary_competency.strip():
            raise ValueError("个性化模块必须选择一个主要能力")
        return self


class HistoryQuestionFilter(BaseModel):
    model_config = ConfigDict(extra="forbid")

    assignment_id: UUID | None = None


class AssignmentFilterConfig(BaseModel):
    """Create-page filter contract for scope and the three filter modules."""

    model_config = ConfigDict(extra="forbid")

    grade_codes: list[str] = Field(min_length=1, max_length=20)
    chapter_codes: list[str] = Field(min_length=1, max_length=20)
    required: RequiredQuestionFilter = Field(default_factory=RequiredQuestionFilter)
    personalized: PersonalizedQuestionFilter = Field(default_factory=PersonalizedQuestionFilter)
    history: HistoryQuestionFilter = Field(default_factory=HistoryQuestionFilter)

    @model_validator(mode="after")
    def unique_scopes(self):
        if sum(self.required.difficulty_counts.values()) < 1:
            raise ValueError("at least one required question is needed")
        if len(set(self.grade_codes)) != len(self.grade_codes):
            raise ValueError("grade_codes must not contain duplicates")
        if len(set(self.chapter_codes)) != len(self.chapter_codes):
            raise ValueError("chapter_codes must not contain duplicates")
        if len(set(self.required.knowledge_point_codes)) != len(self.required.knowledge_point_codes):
            raise ValueError("required knowledge points must not contain duplicates")
        if len(set(self.personalized.knowledge_point_codes)) != len(self.personalized.knowledge_point_codes):
            raise ValueError("personalized knowledge points must not contain duplicates")
        return self


class AssignmentPlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    class_id: UUID | None = None
    student_scope: Literal["selected", "all_class"] = "selected"
    student_ids: list[UUID] = Field(default_factory=list, max_length=2000)
    recommendation_parameters: RecommendationRequest
    filter_config: AssignmentFilterConfig | None = None

    @model_validator(mode="after")
    def unique_students(self):
        if len(set(self.student_ids)) != len(self.student_ids):
            raise ValueError("学生范围不能包含重复项")
        if self.student_scope == "selected" and not self.student_ids:
            raise ValueError("请至少选择一名学生")
        if self.student_scope == "all_class":
            if self.class_id is None:
                raise ValueError("选择全班学生时必须指定班级")
            if self.student_ids:
                raise ValueError("选择全班学生时不应再提交 student_ids")
        return self


class AssignmentCreateRequest(AssignmentPlanRequest):
    idempotency_key: str = Field(min_length=8, max_length=100)
    title: str = Field(min_length=1, max_length=255)

    @field_validator("idempotency_key", "title")
    @classmethod
    def strip_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("字段不能为空")
        return value


class StudentRecommendationPreview(BaseModel):
    student_id: UUID
    student_code: str
    student_name: str
    recommendation: RecommendationOut
    personalized: PersonalizedSelectionPreviewOut | None = None


class AssignmentPreviewOut(BaseModel):
    as_of: datetime
    student_count: int
    ready_count: int
    shortfall_student_count: int
    personalized_reused_question_count: int = 0
    personalized_max_class_usage_count: int = 1
    students: list[StudentRecommendationPreview]


class AssignmentListItem(BaseModel):
    id: UUID
    title: str
    class_id: UUID | None
    class_name: str | None
    chapter_code: str
    assignment_type: str
    student_count: int
    draft_count: int
    published_count: int
    invalidated_count: int
    created_at: datetime


class AssignmentDetailOut(AssignmentListItem):
    teaching_scope: dict
    generation_config: dict | None
    papers: list[PaperSummaryOut]
