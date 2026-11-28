from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.questions import QuestionAnswerItemInput


class S5PreflightSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item_id: UUID
    expected_revision: int = Field(ge=1, strict=True)
    route: Literal["library_only", "direct_pool"]
    answer_review_items: list[QuestionAnswerItemInput] | None = Field(default=None, max_length=50)

    @model_validator(mode="after")
    def answer_draft_matches_route(self):
        if self.route == "library_only" and self.answer_review_items is not None:
            raise ValueError("仅题目入库不接收答案审核草稿")
        return self


class S5PreflightRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[S5PreflightSelection] = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def distinct_items(self):
        ids = [item.item_id for item in self.items]
        if len(ids) != len(set(ids)):
            raise ValueError("同一道候选题不能在一次预检中重复选择")
        return self


class S5BulkItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item_id: UUID
    expected_revision: int = Field(ge=1, strict=True)
    answer_review_items: list[QuestionAnswerItemInput] | None = Field(default=None, max_length=50)


class S5BulkDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["approved", "rejected"]
    route: Literal["library_only", "direct_pool"] | None = None
    reason: str | None = Field(default=None, max_length=500)
    items: list[S5BulkItem] = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def validate_bulk(self):
        if len({item.item_id for item in self.items}) != len(self.items):
            raise ValueError("同一候选题不能重复选择")
        if self.decision == "approved" and self.route is None:
            raise ValueError("批量确认必须选择入库路径")
        if self.decision == "rejected" and (self.route is not None or not (self.reason or "").strip()):
            raise ValueError("批量拒绝必须填写理由，且不能指定入库路径")
        if self.route != "direct_pool" and any(item.answer_review_items is not None for item in self.items):
            raise ValueError("当前操作不能提交答案审核草稿")
        return self


class S5BulkRevokeItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item_id: UUID
    expected_revision: int = Field(ge=1, strict=True)


class S5BulkRevokeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[S5BulkRevokeItem] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_items(self):
        if len({item.item_id for item in self.items}) != len(self.items):
            raise ValueError("同一候选题不能重复选择")
        return self


class S5ItemDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(ge=1, strict=True)
    decision: Literal["approved", "rejected", "pending"]
    route: Literal["library_only", "direct_pool"] | None = None
    reason: str | None = Field(default=None, max_length=500)
    answer_review_items: list[QuestionAnswerItemInput] | None = Field(default=None, max_length=50)

    @model_validator(mode="after")
    def validate_decision(self):
        if self.decision == "approved":
            if self.route is None:
                raise ValueError("确认题目时必须选择入库路径")
            if self.route == "direct_pool" and not self.answer_review_items:
                raise ValueError("直接入池必须提交结构化答案审核")
            if self.route == "library_only" and self.answer_review_items is not None:
                raise ValueError("仅题目入库不接收答案审核")
        elif self.route is not None or self.answer_review_items is not None:
            raise ValueError("拒绝或撤回决定不接收入库路径或答案审核")
        if self.decision == "rejected" and not (self.reason or "").strip():
            raise ValueError("拒绝题目需要填写理由")
        return self


class S5IssueDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    issue_key: str = Field(min_length=1, max_length=240)
    signature: str = Field(min_length=64, max_length=64)
    decision: Literal["acknowledged", "deferred", "ignored", "pending"]
    reason: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def require_reason(self):
        if self.decision in {"ignored", "deferred"} and not (self.reason or "").strip():
            raise ValueError("忽略或延后问题需要填写理由")
        return self


class S5AnswerClaimRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    issue_key: str = Field(min_length=1, max_length=240)
    signature: str = Field(min_length=64, max_length=64)
    item_id: UUID
    expected_revision: int = Field(ge=1, strict=True)


class S5AnswerSourceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(ge=1, strict=True)
    selected_answer_id: str = Field(min_length=1, max_length=180)
    reason: str | None = Field(default=None, max_length=500)
