"""Offline S3 preparation contracts; these are not persisted print artifacts."""
from typing import Literal

from pydantic import Field, model_serializer, model_validator

from app.schemas.assessment import StrictModel, VisionRequest


class PreparationSlot(StrictModel):
    slot_key: str
    paper_instance_item_id: str
    item_id: str
    order_no: int = Field(ge=1)
    subquestion_no: int = Field(ge=1)
    blank_no: int = Field(ge=1)
    source_module: str
    answer_format: Literal["choice_letter", "true_false_mark", "math_or_text"] | None
    allowed_option_labels: list[str] | None
    blocker: Literal["MANUAL_SLOT", "CHOICE_LABELS_MISSING"] | None


class VisionInventory(StrictModel):
    schema_version: Literal["stage6-vision-inventory-v1"] = "stage6-vision-inventory-v1"
    paper_instance_id: str
    instance_code: str
    version_no: int = Field(ge=1)
    layout_version: Literal["answer-card-v2"] = "answer-card-v2"
    slots: list[PreparationSlot] = Field(min_length=1)
    inventory_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    # v2 has no frozen PDF/page manifest. This must never authorize submission.
    formal_recognition_ready: Literal[False] = False


class PageAssignment(StrictModel):
    page_no: int = Field(ge=1, le=100)
    slot_keys: list[str] = Field(min_length=1, max_length=500)


class TrialPage(StrictModel):
    page_no: int = Field(ge=1)
    request: VisionRequest
    slot_bindings: dict[str, str]

    def provider_payload(self) -> dict:
        return self.request.model_dump(exclude_unset=True)

    @model_serializer(mode="wrap")
    def serialize_page(self, handler):
        result = handler(self)
        # Non-choice wire slots must omit allowed_option_labels altogether.
        # Keep normal plan JSON exports valid for a strict save/load roundtrip.
        result["request"] = self.provider_payload()
        return result


class VisionTrialPlan(StrictModel):
    schema_version: Literal["stage6-vision-trial-plan-v1"] = "stage6-vision-trial-plan-v1"
    purpose: Literal["manual_internal_trial"] = "manual_internal_trial"
    inventory: VisionInventory
    pages: list[TrialPage] = Field(min_length=1, max_length=100)


class IdentityConfirmation(StrictModel):
    reviewer: str = Field(min_length=1, max_length=80)
    reason: str = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def not_blank(self):
        if not self.reviewer.strip() or not self.reason.strip():
            raise ValueError("页身份确认必须记录核对人和原因")
        return self


class TrialPageResult(StrictModel):
    page_no: int = Field(ge=1)
    image_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    raw_response: str = Field(min_length=1, max_length=262144)
    identity_confirmation: IdentityConfirmation | None = None
