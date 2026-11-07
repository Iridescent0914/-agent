from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.questions import AssetOut
from app.schemas.recommendations import RecommendedQuestion


class PaperItemOut(BaseModel):
    id: UUID
    question_id: UUID
    order_no: int
    question_snapshot: dict[str, Any]
    options_snapshot: Any | None
    answer_snapshot: Any
    answer_rule_snapshot: dict[str, Any]
    knowledge_point_snapshot: dict[str, Any]
    assets: list[AssetOut]
    selection_audit: dict[str, Any] | None


class StudentPaperItemOut(BaseModel):
    id: UUID
    order_no: int
    question_snapshot: dict[str, Any]
    options_snapshot: Any | None
    knowledge_point_snapshot: dict[str, Any]
    assets: list[AssetOut]


class PaperSummaryOut(BaseModel):
    id: UUID
    assignment_id: UUID
    student_id: UUID
    student_code: str
    student_name: str
    version_no: int
    status: str
    item_count: int
    requested_count: int | None
    shortfall: int | None
    edit_revision: int
    instance_code: str | None
    supersedes_instance_id: UUID | None
    superseded_by_instance_id: UUID | None
    is_active: bool
    has_graded_attempts: bool
    can_withdraw: bool
    withdraw_block_reason: str | None
    invalidated_at: datetime | None
    invalidation_type: str | None
    invalidation_reason: str | None
    invalidated_by: str | None
    created_at: datetime
    published_at: datetime | None


class PaperInstanceTeacherOut(PaperSummaryOut):
    recommendation_audit: dict[str, Any] | None
    edit_history: list[dict[str, Any]]
    items: list[PaperItemOut]


class PaperInstanceStudentOut(BaseModel):
    id: UUID
    assignment_id: UUID
    student_id: UUID
    student_code: str
    version_no: int
    status: str
    instance_code: str | None
    is_active: bool
    invalidated_at: datetime | None
    invalidation_type: str | None
    invalidation_reason: str | None
    superseded_by_instance_id: UUID | None
    items: list[StudentPaperItemOut]


class PaperByCodeOut(BaseModel):
    id: UUID
    assignment_id: UUID
    assignment_title: str
    student_id: UUID
    student_code: str
    student_name: str
    version_no: int
    status: str
    instance_code: str
    item_count: int
    published_at: datetime
    is_active: bool
    invalidated_at: datetime | None
    invalidation_type: str | None
    invalidation_reason: str | None
    superseded_by_instance_id: UUID | None


class DraftMutationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_edit_revision: int = Field(ge=1, strict=True)


class WithdrawPaperRequest(DraftMutationRequest):
    reason: str = Field(min_length=1, max_length=500)


class ReplaceItemRequest(DraftMutationRequest):
    replacement_question_id: UUID


class ReorderItemsRequest(DraftMutationRequest):
    item_ids: list[UUID] = Field(min_length=1, max_length=30)

    @model_validator(mode="after")
    def unique_items(self):
        if len(set(self.item_ids)) != len(self.item_ids):
            raise ValueError("题目顺序不能包含重复项")
        return self


class PublishPaperRequest(DraftMutationRequest):
    acknowledge_shortfall: bool = False
    acknowledgement_reason: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def reason_required(self):
        if self.acknowledge_shortfall and not (self.acknowledgement_reason or "").strip():
            raise ValueError("接受题量不足时必须填写原因")
        return self


class BatchPublishTarget(PublishPaperRequest):
    paper_instance_id: UUID


class BatchPublishRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    papers: list[BatchPublishTarget] = Field(min_length=1, max_length=2000)

    @model_validator(mode="after")
    def unique_papers(self):
        ids = [item.paper_instance_id for item in self.papers]
        if len(set(ids)) != len(ids):
            raise ValueError("批量发布不能包含重复试卷")
        return self


class AlternativeDiagnostic(BaseModel):
    source_module: str
    target_difficulty: int | None = None
    literacy_name: str | None = None
    scope_candidate_count: int = 0
    literacy_matched_count: int = 0
    ready_candidate_count: int = 0
    excluded_current_paper_count: int = 0
    excluded_recent_count: int = 0
    available_candidate_count: int = 0
    reason_codes: list[str] = Field(default_factory=list)
    message: str


class AlternativeOut(BaseModel):
    items: list[RecommendedQuestion]
    diagnostic: AlternativeDiagnostic | None = None
