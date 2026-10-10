from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ClassCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    grade: str = Field(default="未设置", min_length=1, max_length=40)
    school_year: str = Field(default="未设置", min_length=1, max_length=40)
    teacher_name: str | None = Field(default=None, max_length=80)

    @field_validator("name", "grade", "school_year")
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        clean = value.strip()
        if not clean:
            raise ValueError("字段不能为空")
        return clean

    @field_validator("teacher_name")
    @classmethod
    def strip_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None


class ClassUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=120)
    grade: str | None = Field(default=None, min_length=1, max_length=40)
    school_year: str | None = Field(default=None, min_length=1, max_length=40)
    teacher_name: str | None = Field(default=None, max_length=80)

    @field_validator("name", "grade", "school_year")
    @classmethod
    def strip_required_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        clean = value.strip()
        if not clean:
            raise ValueError("字段不能为空")
        return clean

    @field_validator("teacher_name")
    @classmethod
    def strip_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None

    @model_validator(mode="after")
    def require_change(self):
        if not self.model_fields_set:
            raise ValueError("至少提供一个要修改的字段")
        return self


class ClassOut(BaseModel):
    id: uuid.UUID
    name: str
    grade: str
    school_year: str
    teacher_name: str | None
    student_count: int
    created_at: datetime
    updated_at: datetime
