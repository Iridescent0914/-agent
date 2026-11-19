"""V2 is additive: keep the S0/S3-A V1 offline contracts unchanged."""
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.assessment import StrictModel, VisionAnswer, VisionSlot


class PageVisionSlot(VisionSlot):
    writing_bbox_pt: list[float] = Field(min_length=4, max_length=4)

    @model_validator(mode="after")
    def valid_box(self):
        import math
        x0, y0, x1, y1 = self.writing_bbox_pt
        if not all(math.isfinite(v) for v in self.writing_bbox_pt) or not (0 <= x0 < x1 and 0 <= y0 < y1):
            raise ValueError("作答框坐标无效")
        return self


class PageVisionRequest(StrictModel):
    schema_version: Literal["stage6-vision-request-v2"]
    request_id: str
    page_token: str = Field(min_length=1, max_length=100)
    page_size_pt: list[float] = Field(min_length=2, max_length=2)
    # A template page with only empty-section headings still needs identity
    # checking, but has no answer slots. Such a page is not a missing answer.
    slots: list[PageVisionSlot] = Field(min_length=0, max_length=500)

    @model_validator(mode="after")
    def valid_page(self):
        import math
        UUID(self.request_id)
        width, height = self.page_size_pt
        if not all(math.isfinite(v) and v > 0 for v in self.page_size_pt):
            raise ValueError("模板尺寸无效")
        tokens = [s.slot_token for s in self.slots]
        coordinates = [(s.order_no, s.subquestion_no, s.blank_no) for s in self.slots]
        if len(set(tokens)) != len(tokens) or len(set(coordinates)) != len(coordinates):
            raise ValueError("槽位令牌或坐标重复")
        if any(s.writing_bbox_pt[2] > width or s.writing_bbox_pt[3] > height for s in self.slots):
            raise ValueError("作答框超过模板范围")
        return self


class PageObservedIdentity(StrictModel):
    # Required nullable fields: these must be observed from pixels, never copied
    # from expected identity supplied in the prompt (which intentionally has none).
    artifact_code: str | None = Field(min_length=1, max_length=32)
    artifact_revision_no: int | None = Field(ge=1)
    instance_code: str | None = Field(min_length=1, max_length=100)
    version_no: int | None = Field(ge=1)
    layout_version: str | None = Field(min_length=1, max_length=64)
    page_no: int | None = Field(ge=1, le=100)
    total_pages: int | None = Field(ge=1, le=100)

    @model_validator(mode="after")
    def valid_page_number(self):
        if self.page_no is not None and self.total_pages is not None and self.page_no > self.total_pages:
            raise ValueError("观察到的页码超过总页数")
        return self


class PageVisionResponse(StrictModel):
    schema_version: Literal["stage6-vision-response-v2"]
    request_id: str
    page_token: str = Field(min_length=1, max_length=100)
    observed_identity: PageObservedIdentity
    answers: list[VisionAnswer] = Field(min_length=0, max_length=500)


class CreatePageVisionTasks(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    expected_collection_revision: int = Field(ge=0, strict=True)
    expected_input_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    reviewer: str = Field(min_length=1, max_length=100)
    reason: str = Field(min_length=2, max_length=500)
    external_images_reviewed: bool = Field(default=False, strict=True)


class PageVisionAction(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    expected_revision_no: int = Field(ge=1, strict=True)
    reviewer: str = Field(min_length=1, max_length=100)
    reason: str = Field(min_length=2, max_length=500)
