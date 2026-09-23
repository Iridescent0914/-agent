from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel


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


class QuestionDetail(QuestionListItem):
    chapter: TagOut
    options: Any | None
    correct_answer: Any
    reference_answer_raw: str
    answer_match_type: str
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
    created_at: datetime
    updated_at: datetime
