from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class CreateAnswerCardArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # Expected paper content version, not the artifact revision or edit counter.
    expected_version_no: int = Field(ge=1, strict=True)


class AnswerCardArtifactOut(BaseModel):
    id: UUID
    paper_instance_id: UUID
    artifact_code: str
    revision_no: int
    layout_version: Literal["answer-card-v3"]
    renderer_version: str
    source_sha256: str
    pdf_sha256: str
    manifest_sha256: str
    total_pages: int
    created_at: datetime
    pdf_url: str
    manifest_url: str
    reused: bool = False
