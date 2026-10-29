from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.profile import EvidenceRef
from app.schemas.recommendations import RecommendedQuestion


class WeeklySummaryPlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    class_id: UUID
    source_assignment_ids: list[UUID] = Field(min_length=1, max_length=6)

    @model_validator(mode="after")
    def unique_sources(self):
        if len(set(self.source_assignment_ids)) != len(self.source_assignment_ids):
            raise ValueError("周总结来源作业不能重复")
        return self


class WeeklySummaryCreateRequest(WeeklySummaryPlanRequest):
    title: str = Field(min_length=1, max_length=255)
    idempotency_key: str = Field(min_length=8, max_length=100)

    @field_validator("title", "idempotency_key")
    @classmethod
    def strip_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("字段不能为空")
        return value


class WeeklySummaryKnowledgePointOut(BaseModel):
    knowledge_point_code: str
    knowledge_point_name: str
    evidence: list[EvidenceRef]


class WeeklySummaryStudentPreview(BaseModel):
    student_id: UUID
    student_code: str
    student_name: str
    false_knowledge_points: list[WeeklySummaryKnowledgePointOut]
    requested_count: int
    selected_count: int
    shortfall: int
    status: str
    items: list[RecommendedQuestion]


class WeeklySummaryPreviewOut(BaseModel):
    as_of: datetime
    student_count: int
    paper_student_count: int
    ready_count: int
    shortfall_student_count: int
    source_assignment_ids: list[UUID]
    involved_knowledge_point_codes: list[str]
    students: list[WeeklySummaryStudentPreview]
