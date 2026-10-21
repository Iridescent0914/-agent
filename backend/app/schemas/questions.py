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


class QuestionAssetInput(BaseModel):
    model_config = ConfigDict(extra='forbid')

    path: str = Field(
        pattern=(
            r'^assets/(?:teacher/[0-9a-f]{32}\.png|'
            r'(?:rational|expr)/[A-Za-z0-9._-]+)$'
        ),
        max_length=255,
    )


class QuestionListItem(BaseModel):
    id: uuid.UUID
    original_qid: str
    question_type: str
    difficulty: int
    grade: str
    section: str
    question_text: str
    main_knowledge_point: TagOut
    core_literacy: str
    is_required: bool
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


class QuestionCatalogKnowledgePoint(TagOut):
    section: str | None
    mastery_level: str | None
    core_competencies: list[str]


class QuestionCatalogChapter(TagOut):
    sections: list[str]
    knowledge_points: list[QuestionCatalogKnowledgePoint]


class QuestionCreateRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')

    original_qid: str | None = Field(default=None, max_length=180)
    question_type: Literal[
        'choice', 'fill_blank', 'calculation', 'solution', 'true_false'
    ]
    difficulty: int = Field(ge=1, le=5)
    grade: str = Field(default='六年级上册', min_length=1, max_length=80)
    section: str = Field(default='教师新增', min_length=1, max_length=255)
    question_text: str = Field(min_length=1, max_length=10000)
    options: list[str] | None = Field(default=None, min_length=2, max_length=8)
    reference_answer_raw: str = Field(min_length=1, max_length=5000)
    main_knowledge_point_code: str = Field(min_length=1, max_length=160)
    solution: str | None = Field(default=None, max_length=10000)
    wrong_analysis: str | None = Field(default=None, max_length=10000)
    estimated_score_rate: str | None = Field(default=None, max_length=64)
    core_literacy: str = Field(default='待标注', min_length=1, max_length=255)
    is_required: bool = False
    operator_name: str = Field(min_length=1, max_length=80)
    assets: list[QuestionAssetInput] = Field(default_factory=list, max_length=64)

    @field_validator(
        'grade', 'section', 'question_text', 'reference_answer_raw', 'core_literacy',
        'main_knowledge_point_code', 'operator_name',
    )
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        clean = value.strip()
        if not clean:
            raise ValueError('字段不能为空')
        return clean

    @field_validator('original_qid', 'solution', 'wrong_analysis', 'estimated_score_rate')
    @classmethod
    def strip_optional_text(cls, value: str | None) -> str | None:
        return value.strip() or None if value is not None else None

    @model_validator(mode='after')
    def validate_options(self):
        if self.question_type == 'choice':
            if not self.options:
                raise ValueError('选择题至少需要两个选项')
            cleaned = [option.strip() for option in self.options]
            if any(not option for option in cleaned):
                raise ValueError('选项不能为空')
            if len(set(cleaned)) != len(cleaned):
                raise ValueError('选项不能重复')
            self.options = cleaned
        else:
            self.options = None
        asset_paths = [asset.path for asset in self.assets]
        if len(asset_paths) != len(set(asset_paths)):
            raise ValueError('题目图片不能重复')
        return self


class QuestionUpdateRequest(QuestionCreateRequest):
    expected_review_revision: int = Field(ge=1, strict=True)


class QuestionArchiveRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')

    operator_name: str = Field(min_length=1, max_length=80)

    @field_validator('operator_name')
    @classmethod
    def strip_operator(cls, value: str) -> str:
        clean = value.strip()
        if not clean:
            raise ValueError('操作人不能为空')
        return clean


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
