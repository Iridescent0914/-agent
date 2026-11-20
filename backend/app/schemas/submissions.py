from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.questions import AssetOut


class ManualAnswerIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    paper_instance_item_id: UUID
    order_no: int = Field(ge=1, strict=True)
    response_status: Literal["answered", "blank"]
    answer: Any | None = None

    @model_validator(mode="after")
    def answer_matches_status(self):
        if self.response_status == "blank" and self.answer is not None:
            raise ValueError("留白题的 answer 必须为 null")
        if self.response_status == "answered" and self.answer is None:
            raise ValueError("已作答题必须提供 answer")
        return self


class ManualSubmissionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    idempotency_key: str = Field(min_length=8, max_length=100)
    student_id: UUID
    answered_at: datetime | None = None
    answers: list[ManualAnswerIn] = Field(min_length=1, max_length=100)

    @field_validator("idempotency_key")
    @classmethod
    def clean_key(cls, value: str) -> str:
        cleaned = value.strip()
        if len(cleaned) < 8:
            raise ValueError("idempotency_key 不能为空")
        return cleaned

    @field_validator("answered_at")
    @classmethod
    def timezone_required(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("answered_at 必须包含时区")
        return value

    @model_validator(mode="after")
    def unique_items(self):
        ids = [item.paper_instance_item_id for item in self.answers]
        orders = [item.order_no for item in self.answers]
        if len(ids) != len(set(ids)) or len(orders) != len(set(orders)):
            raise ValueError("答案中不能包含重复题目或题号")
        return self


class AttemptOut(BaseModel):
    id: UUID
    paper_instance_item_id: UUID
    order_no: int
    student_answer: Any | None
    normalized_answer: Any | None
    grading_details: dict[str, Any]
    grade_status: str
    is_correct: bool | None
    manual_override: bool
    revision_no: int
    answered_at: datetime
    reviewed_at: datetime | None
    used_in_profile: bool
    exclusion_reason: str | None = None
    recognition_status: str | None = None
    ocr_confidence: float | None = None


class SubmissionPaperItemOut(BaseModel):
    id: UUID
    question_id: UUID
    order_no: int
    question_snapshot: dict[str, Any]
    options_snapshot: Any | None
    answer_snapshot: Any
    answer_rule_snapshot: dict[str, Any]
    grading_answer_snapshot: Any
    grading_answer_rule_snapshot: dict[str, Any]
    standard_answer_source: Literal["published_snapshot", "question_revision"]
    answer_key_update_available: bool
    knowledge_point_snapshot: dict[str, Any]
    assets: list[AssetOut]
    attempt: AttemptOut | None


class SubmissionCounts(BaseModel):
    total_count: int
    correct_count: int
    incorrect_count: int
    needs_review_count: int


class SubmissionDetailOut(BaseModel):
    answer_transcription: dict[str, Any] | None = None
    submission_id: UUID | None
    paper_instance_id: UUID
    assignment_id: UUID
    assignment_title: str
    instance_code: str | None
    version_no: int
    paper_status: str
    is_active: bool
    can_submit: bool
    student_id: UUID
    student_code: str
    student_name: str
    source: str | None
    recognition_run_id: UUID | None = None
    recognition_image_url: str | None = None
    recognition_image_purged_at: datetime | None = None
    recognition_provider: str | None = None
    recognition_model: str | None = None
    recognition_prompt_version: str | None = None
    submitted_at: datetime | None
    replayed: bool = False
    unchanged: bool = False
    answer_key_update_count: int = 0
    regraded_count: int = 0
    summary: SubmissionCounts
    items: list[SubmissionPaperItemOut]
    mastery_update_stats: dict[str, int] | None = None


class AttemptReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["confirm", "correct", "reopen"]
    expected_revision_no: int = Field(ge=1, strict=True)
    is_correct: bool | None = None
    corrected_response_status: Literal["answered", "blank"] | None = None
    corrected_answer: Any | None = None
    reason: str = Field(min_length=2, max_length=500)
    actor: str = Field(default="demo_teacher", min_length=1, max_length=80)

    @model_validator(mode="after")
    def valid_action_fields(self):
        self.reason = self.reason.strip()
        self.actor = self.actor.strip()
        if self.action in {"confirm", "correct"} and self.is_correct is None:
            raise ValueError("确认或修正时必须提供 is_correct")
        if self.action == "reopen" and self.is_correct is not None:
            raise ValueError("撤销确认时 is_correct 必须为 null")
        if self.action == "correct":
            if self.corrected_response_status is None:
                raise ValueError("修正答案时必须提供 corrected_response_status")
            if self.corrected_response_status == "blank" and self.corrected_answer is not None:
                raise ValueError("修正为留白时 corrected_answer 必须为 null")
            if self.corrected_response_status == "answered" and self.corrected_answer is None:
                raise ValueError("修正为已作答时必须提供 corrected_answer")
        elif self.corrected_response_status is not None or self.corrected_answer is not None:
            raise ValueError("只有 correct 操作可以修改学生答案")
        return self


class SubmissionRegradeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(default="标准答案更新后重新评分", min_length=2, max_length=500)
    actor: str = Field(default="demo_teacher", min_length=1, max_length=80)

    @model_validator(mode="after")
    def clean_fields(self):
        self.reason = self.reason.strip()
        self.actor = self.actor.strip()
        if len(self.reason) < 2 or not self.actor:
            raise ValueError("重新评分原因和操作人不能为空")
        return self


class AttemptRevisionOut(BaseModel):
    revision_no: int
    before_state: dict[str, Any] | None
    after_state: dict[str, Any]
    operator_type: str
    operator_id: str | None
    reason: str
    created_at: datetime


class GradingQueueItem(BaseModel):
    paper_instance_id: UUID
    assignment_id: UUID
    assignment_title: str
    class_id: UUID | None
    class_name: str | None
    student_id: UUID
    student_code: str
    student_name: str
    version_no: int
    instance_code: str
    is_active: bool
    invalidation_type: str | None
    grading_status: Literal["unsubmitted", "needs_review", "completed"]
    item_count: int
    needs_review_count: int
    submitted_at: datetime | None


class GradingQueueOut(BaseModel):
    items: list[GradingQueueItem]
    total: int
