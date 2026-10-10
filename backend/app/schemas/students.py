from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StudentCreateRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')

    student_code: str = Field(
        min_length=1, max_length=32, pattern=r'^[A-Za-z0-9._-]+$'
    )
    display_name: str = Field(min_length=1, max_length=80)
    class_id: uuid.UUID | None = None
    class_name: str | None = Field(default=None, min_length=1, max_length=120)

    @field_validator('student_code', 'display_name')
    @classmethod
    def strip_text(cls, value: str) -> str:
        clean = value.strip()
        if not clean:
            raise ValueError('字段不能为空')
        return clean

    @field_validator('class_name')
    @classmethod
    def strip_class_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        clean = value.strip()
        if not clean:
            raise ValueError('班级名称不能为空')
        return clean

    @model_validator(mode='after')
    def require_class(self):
        if self.class_id is None and self.class_name is None:
            raise ValueError('必须选择班级')
        return self


class StudentArchiveRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')

    operator_name: str = Field(min_length=1, max_length=80)

    @field_validator('operator_name')
    @classmethod
    def strip_operator(cls, value: str) -> str:
        clean = value.strip()
        if not clean:
            raise ValueError('操作人不能为空')
        return clean


class StudentClassUpdateRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')

    class_id: uuid.UUID


class StudentListItem(BaseModel):
    id: uuid.UUID
    student_code: str
    display_name: str
    class_id: uuid.UUID | None
    class_name: str
    is_demo: bool
    attempt_count: int
    correct_count: int
    latest_answered_at: datetime | None


class StudentDetail(StudentListItem):
    created_at: datetime
    evidence_note: str = "作答证据量按确认作答次数分档，并非统计置信概率；掌握度仅作教学参考。"


class StudentAttemptOut(BaseModel):
    id: uuid.UUID
    answered_at: datetime
    original_qid: str
    question_id: uuid.UUID
    question_text: str
    difficulty: int | None
    main_exam_point_code: str
    main_exam_point_name: str
    student_answer: Any | None
    grade_status: str
    is_correct: bool | None
    paper_instance_id: uuid.UUID | None
    paper_instance_item_id: uuid.UUID
    revision_no: int
    used_in_profile: bool
    exclusion_reason: str | None
