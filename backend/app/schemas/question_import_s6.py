from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class S6CommitSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item_id: UUID
    expected_revision: int = Field(ge=1, strict=True)
    question_event_id: UUID


class S6PreflightRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[S6CommitSelection] = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def distinct_items(self):
        if len({item.item_id for item in self.items}) != len(self.items):
            raise ValueError("同一候选题不能重复提交")
        return self


class S6CommitRequest(S6PreflightRequest):
    operation_id: UUID
