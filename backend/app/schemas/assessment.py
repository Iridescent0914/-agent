from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class EquivalenceRequest(StrictModel):
    schema_version: Literal["stage6-equivalence-request-v1"]
    request_id: str
    reference_answer: str = Field(min_length=1, max_length=16000)
    student_answer: str = Field(min_length=1, max_length=16000)

    @field_validator("request_id")
    @classmethod
    def valid_uuid(cls, value: str) -> str:
        UUID(value)
        return value

    @field_validator("reference_answer", "student_answer")
    @classmethod
    def not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("答案不可留白")
        return value


Verdict = Literal["equivalent", "not_equivalent", "uncertain"]


class EquivalenceResponse(StrictModel):
    schema_version: Literal["stage6-equivalence-response-v1"]
    request_id: str
    verdict: Verdict
    reason_code: Literal["equivalent_form", "different_value_or_expression", "context_required", "unparseable_answer", "unsupported_answer"]
    explanation: str = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def consistent_reason(self):
        allowed = {
            "equivalent": {"equivalent_form"},
            "not_equivalent": {"different_value_or_expression"},
            "uncertain": {"context_required", "unparseable_answer", "unsupported_answer"},
        }
        if self.reason_code not in allowed[self.verdict] or not self.explanation.strip():
            raise ValueError("结论与原因不一致或说明为空")
        return self


class CreateAssessmentTask(BaseModel):
    model_config = ConfigDict(extra="forbid")
    paper_instance_item_id: UUID
    item_id: str = Field(min_length=1, max_length=160)
    # Only an internal draft: submitted answers are always read from Attempt.
    student_answer: str | None = Field(default=None, max_length=16000, strict=True)
    expected_input_revision: int = Field(default=0, ge=0, strict=True)
    expected_attempt_revision: int | None = Field(default=None, ge=1, strict=True)


class TaskAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(min_length=1, max_length=1000)

    @field_validator("reason")
    @classmethod
    def not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("必须提供操作原因")
        return value


RecognitionStatus = Literal["readable", "blank", "missing", "illegible", "ambiguous"]


class RecognitionGold(StrictModel):
    recognition_status: RecognitionStatus
    answer: str | None = Field(max_length=16000)

    @model_validator(mode="after")
    def consistent_answer(self):
        if self.recognition_status == "readable":
            if not self.answer or not self.answer.strip():
                raise ValueError("可读答案必须提供忠实抄录文本")
        elif self.answer is not None:
            raise ValueError("非可读状态的答案必须为 null")
        return self


class VisionSlot(StrictModel):
    slot_token: str = Field(min_length=1, max_length=100)
    order_no: int = Field(ge=1)
    subquestion_no: int = Field(ge=1)
    blank_no: int = Field(ge=1)
    answer_format: Literal["choice_letter", "true_false_mark", "math_or_text"]
    allowed_option_labels: list[str] | None = Field(default=None, min_length=1, max_length=26)

    @model_validator(mode="after")
    def valid_choices(self):
        labels = self.allowed_option_labels
        if self.answer_format == "choice_letter":
            if not labels or len(set(labels)) != len(labels) or any(len(x) != 1 or not "A" <= x <= "Z" for x in labels):
                raise ValueError("选择空须提供实际允许的不重复选项字母")
        elif "allowed_option_labels" in self.model_fields_set:
            raise ValueError("非选择空不得提供选项列表")
        return self


class VisionRequest(StrictModel):
    schema_version: Literal["stage6-vision-request-v1"]
    request_id: str
    page_token: str = Field(min_length=1, max_length=100)
    slots: list[VisionSlot] = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def unique_slots(self):
        UUID(self.request_id)
        tokens = [s.slot_token for s in self.slots]
        coordinates = [(s.order_no, s.subquestion_no, s.blank_no) for s in self.slots]
        if len(set(tokens)) != len(tokens) or len(set(coordinates)) != len(coordinates):
            raise ValueError("槽位令牌或坐标重复")
        return self


class ObservedIdentity(StrictModel):
    instance_code: str | None = Field(min_length=1, max_length=100)
    version_no: int | None = Field(ge=1)
    page_no: int | None = Field(ge=1)
    total_pages: int | None = Field(ge=1)

    @model_validator(mode="after")
    def page_in_range(self):
        if self.page_no is not None and self.total_pages is not None and self.page_no > self.total_pages:
            raise ValueError("观察到的页码超过总页数")
        return self


class VisionAnswer(RecognitionGold):
    slot_token: str = Field(min_length=1, max_length=100)
    confidence: float | None = Field(ge=0, le=1, allow_inf_nan=False)


class VisionResponse(StrictModel):
    schema_version: Literal["stage6-vision-response-v1"]
    request_id: str
    page_token: str
    observed_identity: ObservedIdentity
    answers: list[VisionAnswer] = Field(min_length=1, max_length=500)


class EquivalenceGold(StrictModel):
    verdict: Verdict


class QualityFeedbackIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=0, strict=True)
    review_status: Literal["correct", "incorrect", "uncertain"]
    error_type: Literal["transcription_error", "slot_mismatch", "blank_confusion", "equivalence_error", "reference_issue", "input_changed", "evidence_insufficient"] | None = None
    gold: RecognitionGold | EquivalenceGold | None = None
    reviewer: str = Field(min_length=1, max_length=80)
    reason: str = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def feedback_consistent(self):
        if not self.reviewer.strip() or not self.reason.strip():
            raise ValueError("核对人和原因不可为空")
        model_errors = {"transcription_error", "slot_mismatch", "blank_confusion", "equivalence_error"}
        if self.review_status == "incorrect":
            if self.error_type not in model_errors or self.gold is None:
                raise ValueError("确认错误须提供模型错误类型和人工正确结果")
        elif self.review_status == "correct":
            if self.error_type is not None:
                raise ValueError("确认正确不能同时标记错误")
        elif self.error_type not in {"reference_issue", "input_changed", "evidence_insufficient"}:
            raise ValueError("无法确定须注明问题归因")
        return self
