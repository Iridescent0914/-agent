from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel


class ClassOption(BaseModel):
    class_name: str
    student_count: int


class ClassAnalyticsSummary(BaseModel):
    student_count: int
    evidence_student_count: int
    confirmed_attempt_count: int
    pending_review_count: int
    invalid_evidence_count: int
    covered_exam_point_count: int
    uncovered_exam_point_count: int


class ClassExamPointAnalytics(BaseModel):
    exam_point_id: UUID
    exam_point_code: str
    exam_point_name: str
    student_count: int
    evidence_student_count: int
    stable_student_count: int
    unassessed_student_count: int
    confirmed_attempt_count: int
    correct_attempt_count: int
    incorrect_attempt_count: int
    accuracy: float | None
    average_mastery: float | None
    recent_wrong_count: int
    available_question_count: int


class ClassFocusPoint(BaseModel):
    exam_point_code: str
    exam_point_name: str
    category: Literal["practice", "recent_errors", "observe", "diagnostic"]
    reason: str


class ClassOverviewOut(BaseModel):
    class_name: str
    chapter_code: str
    computed_at: datetime
    rule_version: str
    summary: ClassAnalyticsSummary
    exam_points: list[ClassExamPointAnalytics]
    focus_points: list[ClassFocusPoint]
    evidence_note: str = (
        "正确率只统计有效确认作答；待复核不算错误；"
        "未评估学生不参与掌握度均值。"
    )


class ExamPointStudentAnalytics(BaseModel):
    student_id: UUID
    student_code: str
    student_name: str
    assessment_status: Literal["unassessed", "insufficient", "assessed"]
    mastery: float | None
    attempt_count: int
    correct_count: int
    accuracy: float | None
    recent_wrong_count: int
    last_practiced_at: datetime | None


class ExamPointStudentListOut(BaseModel):
    class_name: str
    chapter_code: str
    exam_point_code: str
    exam_point_name: str
    computed_at: datetime
    items: list[ExamPointStudentAnalytics]


class AssignmentProgressSummary(BaseModel):
    student_count: int
    draft_count: int
    active_published_count: int
    historical_invalidated_count: int
    submitted_count: int
    unsubmitted_count: int
    needs_review_count: int
    completed_count: int
    pending_attempt_count: int
    submission_rate: float | None
    completion_rate: float | None


class AssignmentProgressStudent(BaseModel):
    paper_instance_id: UUID
    student_id: UUID
    student_code: str
    student_name: str
    version_no: int
    instance_code: str
    item_count: int
    grading_status: Literal["unsubmitted", "needs_review", "completed"]
    needs_review_count: int
    submitted_at: datetime | None


class AssignmentAnalyticsOut(BaseModel):
    assignment_id: UUID
    assignment_title: str
    chapter_code: str
    computed_at: datetime
    summary: AssignmentProgressSummary
    students: list[AssignmentProgressStudent]

