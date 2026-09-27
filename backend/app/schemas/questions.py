from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class TagOut(BaseModel):
    code: str
    name: str


class AssetOut(BaseModel):
    path: str
    web_url: str | None = None
    conversion_status: str


class QuestionListItem(BaseModel):
    id: uuid.UUID
    original_qid: str
    question_type: str
    difficulty: int
    section: str
    question_text: str
    main_knowledge_point: TagOut
    main_exam_point: TagOut
    has_image: bool
    has_equation: bool
    asset_count: int
    grading_readiness: str
    import_status: str
    import_issue_count: int


class QuestionCatalogStats(BaseModel):
    total: int
    ready_count: int
    pending_count: int
    type_counts: dict[str, int]


class QuestionAnswerItemInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item_id: str = Field(min_length=1, max_length=64)
    answer: str = Field(min_length=1, max_length=2000)
    answer_match_type: Literal["choice", "numeric", "exact", "latex_exact"]

    @field_validator("item_id", "answer")
    @classmethod
    def strip_nonempty(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("答案项编号和答案不能为空")
        return value


class QuestionReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_review_revision: int = Field(ge=1, strict=True)
    reviewer_name: str = Field(min_length=1, max_length=80)
    review_note: str | None = Field(default=None, max_length=500)
    items: list[QuestionAnswerItemInput] = Field(min_length=1, max_length=50)

    @field_validator("reviewer_name")
    @classmethod
    def strip_reviewer(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("审核人不能为空")
        return value

    @model_validator(mode="after")
    def unique_item_ids(self):
        item_ids = [item.item_id for item in self.items]
        if len(set(item_ids)) != len(item_ids):
            raise ValueError("答案项编号不能重复")
        return self


class QuestionWithdrawRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_review_revision: int = Field(ge=1, strict=True)
    reviewer_name: str = Field(min_length=1, max_length=80)
    review_note: str | None = Field(default=None, max_length=500)

    @field_validator("reviewer_name")
    @classmethod
    def strip_reviewer(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("操作人不能为空")
        return value


class QuestionDetail(QuestionListItem):
    chapter: TagOut
    options: Any | None
    correct_answer: Any | None
    reference_answer_raw: str
    answer_match_type: str | None
    answer_normalization_rule: dict[str, Any]
    related_exam_points: list[TagOut]
    solution: str | None
    wrong_analysis: str | None
    estimated_score_rate: str | None
    assets: list[AssetOut]
    source_file: str
    source_question_number: int
    source_review_status: str
    grading_readiness: str
    import_status: str
    import_issues: list[str]
    review_revision: int
    review_history: list[dict[str, Any]]
    reviewed_by: str | None
    reviewed_at: datetime | None
    created_at: datetime
    updated_at: datetime
