from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class SavePaperRecognitionReview(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    expected_revision_no: int = Field(ge=0, strict=True)
    expected_recognition_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    review_status: Literal["pending", "correct", "incorrect", "uncertain"]
    all_pages_reviewed: bool = Field(default=False, strict=True)
    reviewer: str = Field(min_length=1, max_length=100)
    reason: str = Field(min_length=2, max_length=500)
