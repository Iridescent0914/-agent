from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.papers import PaperSummaryOut
from app.schemas.recommendations import RecommendationOut, RecommendationRequest


class AssignmentPlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    class_id: UUID | None = None
    student_scope: Literal["selected", "all_class"] = "selected"
    student_ids: list[UUID] = Field(default_factory=list, max_length=2000)
    recommendation_parameters: RecommendationRequest

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


class AssignmentPreviewOut(BaseModel):
    as_of: datetime
    student_count: int
    ready_count: int
    shortfall_student_count: int
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
