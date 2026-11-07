"""Structural admission checks shared by data repair and teacher promotion."""
from __future__ import annotations

import json
import re
from typing import Any

from app.services.answer_units import AUTO_MATCH_TYPES, is_canonical_answer_structure
from app.services.grading.engine import grade_answer


def pool_answer_issues(answer: Any, match_type: str | None, rules: dict[str, Any]) -> list[str]:
    if not is_canonical_answer_structure(answer):
        return ["invalid_answer_structure"]
    issues = []
    items = answer["items"]
    if not items or any(not isinstance(item.get("answer"), str) or not item["answer"].strip() for item in items):
        issues.append("missing_answer_units")
    if any(item.get("answer_match_type") not in AUTO_MATCH_TYPES for item in items):
        issues.append("non_automatic_answer_units")
    if "[ASSET:" in json.dumps(answer, ensure_ascii=False):
        issues.append("image_in_structured_answer")
    if any(re.search(r"AI\s*无法识别|请合作者|题面信息疑似残缺", str(item.get("answer", ""))) for item in items):
        issues.append("placeholder_in_answer")
    result = grade_answer(answer, answer, {"answer_match_type": match_type, "answer_normalization_rule": rules}, response_status="answered")
    if result.is_correct is not True:
        issues.append("standard_answer_does_not_self_grade")
    return issues
