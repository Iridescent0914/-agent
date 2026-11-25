from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel


class AccuracySampleOut(BaseModel):
    confirmed_attempt_count: int
    correct_count: int
    incorrect_count: int
    distinct_question_count: int
    pending_review_count: int
    excluded_attempt_count: int
    accuracy: float | None


class PaperAccuracySampleOut(AccuracySampleOut):
    total_question_count: int
    unsubmitted_question_count: int
    judged_coverage: float | None


class KnowledgeHistoryOut(BaseModel):
    knowledge_point_code: str | None
    knowledge_point_name: str
    paper_question_count: int
    historical_confirmed_attempt_count: int
    history_status: Literal["seen", "unseen", "unmapped"]


class KnowledgeHistoryCoverageOut(BaseModel):
    seen_question_count: int
    unseen_question_count: int
    unmapped_question_count: int
    points: list[KnowledgeHistoryOut]


class PaperAccuracyComparisonOut(BaseModel):
    paper_instance_id: UUID
    computed_at: datetime
    counting_unit: Literal["attempt_question_group"] = "attempt_question_group"
    history_scope: Literal["all_history_excluding_current_paper"] = "all_history_excluding_current_paper"
    read_only: Literal[True] = True
    data_status: Literal["inactive_paper", "no_paper_results", "no_history", "incomplete_paper", "available"]
    paper: PaperAccuracySampleOut
    history: AccuracySampleOut
    knowledge_coverage: KnowledgeHistoryCoverageOut
