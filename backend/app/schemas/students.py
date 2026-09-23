from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel


class StudentListItem(BaseModel):
    id: uuid.UUID
    student_code: str
    display_name: str
    class_name: str
    is_demo: bool
    attempt_count: int
    correct_count: int
    latest_answered_at: datetime | None


class StudentDetail(StudentListItem):
    created_at: datetime
    evidence_note: str = "M1 仅展示历史作答证据，掌握度和薄弱点将在 M2 计算。"


class StudentAttemptOut(BaseModel):
    id: uuid.UUID
    answered_at: datetime
    original_qid: str
    question_id: uuid.UUID
    question_text: str
    difficulty: int
    main_exam_point_code: str
    main_exam_point_name: str
    student_answer: Any | None
    grade_status: str
    is_correct: bool | None
    paper_instance_id: uuid.UUID
    paper_instance_item_id: uuid.UUID
