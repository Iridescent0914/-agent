from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

RecognitionStatus = Literal["readable", "blank", "missing", "illegible", "ambiguous"]
RunStatus = Literal["uploaded", "processing", "ready_for_review", "failed", "committed", "discarded"]


class PrintItemOut(BaseModel):
    id: UUID
    order_no: int
    question_snapshot: dict[str, Any]
    options_snapshot: Any | None
    assets: list[dict[str, Any]]


class AnswerSlotOut(BaseModel):
    order_no: int
    blank_ids: list[str]
    kind: str


class PrintViewOut(BaseModel):
    paper_instance_id: UUID
    assignment_title: str
    student_code: str
    version_no: int
    instance_code: str
    layout_version: str = "m6-print-v1"
    is_active: bool
    items: list[PrintItemOut]
    answer_slots: list[AnswerSlotOut]


class VisionAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    order_no: int = Field(ge=1, strict=True)
    answer: Any | None = None
    recognition_status: RecognitionStatus
    confidence: float | None = Field(default=None, ge=0, le=1)

    @model_validator(mode="after")
    def answer_matches_status(self):
        if self.recognition_status == "readable" and self.answer is None:
            raise ValueError("readable 必须包含 answer")
        if self.recognition_status == "blank" and self.answer is not None:
            raise ValueError("blank 的 answer 必须为 null")
        return self


class VisionExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # v1 remains readable so previously stored provider payloads and external
    # evaluation fixtures can still be inspected after the prompt upgrade.
    schema_version: Literal["m6-recognition-v1", "m6-recognition-v2"]
    answers: list[VisionAnswer] = Field(max_length=100)

    @model_validator(mode="after")
    def unique_orders(self):
        orders = [item.order_no for item in self.answers]
        if len(orders) != len(set(orders)):
            raise ValueError("识别结果不能包含重复题号")
        return self


class RecognitionItemOut(BaseModel):
    id: UUID
    paper_instance_item_id: UUID
    order_no: int
    recognition_status: RecognitionStatus
    recognized_answer: Any | None
    confidence: float | None


class RecognitionRunOut(BaseModel):
    id: UUID
    paper_instance_id: UUID
    student_id: UUID
    submission_id: UUID | None
    status: RunStatus
    provider: str
    model: str
    prompt_version: str
    original_filename: str | None
    content_type: str
    byte_size: int
    pixel_width: int
    pixel_height: int
    error_code: str | None
    error_message: str | None
    created_at: datetime
    completed_at: datetime | None
    started_at: datetime | None
    processing_stale: bool = False
    image_purged_at: datetime | None
    items: list[RecognitionItemOut]
    image_url: str | None


class RecognitionReviewItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    attempt_id: UUID
    expected_revision_no: int = Field(ge=1, strict=True)
    action: Literal["accept", "correct"]
    response_status: Literal["answered", "blank"]
    answer: Any | None = None

    @model_validator(mode="after")
    def answer_matches_status(self):
        if self.response_status == "blank" and self.answer is not None:
            raise ValueError("blank 的 answer 必须为 null")
        if self.response_status == "answered" and self.answer is None:
            raise ValueError("answered 必须包含 answer")
        return self


class RecognitionReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(min_length=2, max_length=500)
    actor: str = Field(default="demo_teacher", min_length=1, max_length=80)
    items: list[RecognitionReviewItem] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def unique_attempts(self):
        ids = [item.attempt_id for item in self.items]
        if len(ids) != len(set(ids)):
            raise ValueError("不能重复确认同一作答")
        self.reason = self.reason.strip()
        self.actor = self.actor.strip()
        return self
