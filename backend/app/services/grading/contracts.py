from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


ENGINE_VERSION = "m4-grading-v1"
GradeStatus = Literal["auto_confirmed", "needs_review"]


@dataclass(frozen=True)
class GradeResult:
    normalized_answer: Any | None
    grade_status: GradeStatus
    is_correct: bool | None
    reason_code: str
    match_type: str
    rule: dict[str, Any] = field(default_factory=dict)
    item_results: tuple[dict[str, Any], ...] = ()
    warnings: tuple[str, ...] = ()

    def details(self) -> dict[str, Any]:
        return {
            "engine_version": ENGINE_VERSION,
            "match_type": self.match_type,
            "auto_status": self.grade_status,
            "auto_is_correct": self.is_correct,
            "reason_code": self.reason_code,
            "rule": self.rule,
            "item_results": list(self.item_results),
            "warnings": list(self.warnings),
        }
