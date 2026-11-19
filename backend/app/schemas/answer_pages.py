from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class CreatePageCollection(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    artifact_code: str = Field(min_length=1, max_length=32)
    instance_code: str = Field(min_length=1, max_length=100)
    expected_version_no: int = Field(ge=1, strict=True)


class SelectPageImage(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    expected_revision_no: int = Field(ge=0, strict=True)
    image_id: UUID | None
    reviewer: str = Field(min_length=1, max_length=100)
    reason: str = Field(min_length=2, max_length=500)
