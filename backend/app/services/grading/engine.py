from __future__ import annotations

from typing import Any

from app.services.grading.contracts import GradeResult
from app.services.grading.normalize import NormalizationError, normalize_choice, normalize_text
from app.services.grading.numeric import NumericParseError, parse_numeric


SUPPORTED_MATCH_TYPES = frozenset({"choice", "exact", "numeric", "latex_exact", "group"})


def _pending(match_type: str, reason: str, rule: dict[str, Any], *,
             normalized: Any | None = None, item_results: tuple[dict[str, Any], ...] = ()) -> GradeResult:
    return GradeResult(normalized, "needs_review", None, reason, match_type, rule, item_results)


def _confirmed(match_type: str, correct: bool, reason: str, rule: dict[str, Any], *,
               normalized: Any | None, item_results: tuple[dict[str, Any], ...] = ()) -> GradeResult:
    return GradeResult(normalized, "auto_confirmed", correct, reason, match_type, rule, item_results)


def _grade_choice(student_answer: Any, expected: Any, rule: dict[str, Any]) -> GradeResult:
    try:
        normalized = normalize_choice(student_answer, rule)
        normalized_expected = normalize_choice(expected, rule)
    except NormalizationError:
        return _pending("choice", "invalid_choice_format", rule)
    correct = normalized == normalized_expected
    return _confirmed("choice", correct, "matched" if correct else "mismatched", rule,
                      normalized=normalized)


def _grade_exact(student_answer: Any, expected: Any, rule: dict[str, Any]) -> GradeResult:
    try:
        normalized = normalize_text(student_answer, rule)
        normalized_expected = normalize_text(expected, rule)
    except NormalizationError:
        return _pending("exact", "invalid_answer_type", rule)
    correct = normalized == normalized_expected
    return _confirmed("exact", correct, "matched" if correct else "mismatched", rule,
                      normalized=normalized)


def _grade_latex_exact(student_answer: Any, expected: Any, rule: dict[str, Any]) -> GradeResult:
    try:
        normalized = normalize_text(student_answer, rule)
        normalized_expected = normalize_text(expected, rule)
    except NormalizationError:
        return _pending("latex_exact", "invalid_answer_type", rule)
    if normalized == normalized_expected:
        return _confirmed("latex_exact", True, "matched", rule, normalized=normalized)
    return _pending("latex_exact", "formula_not_exact_match", rule, normalized=normalized)


def _grade_numeric(student_answer: Any, expected: Any, rule: dict[str, Any]) -> GradeResult:
    if rule.get("require_unit", False):
        return _pending("numeric", "invalid_rule_snapshot", rule)
    try:
        expected_number = parse_numeric(expected, rule)
    except NumericParseError:
        return _pending("numeric", "invalid_rule_snapshot", rule)
    try:
        actual = parse_numeric(student_answer, rule)
    except NumericParseError as exc:
        return _pending("numeric", exc.reason_code, rule)

    if actual.value != expected_number.value:
        return _confirmed("numeric", False, "mismatched", rule, normalized=actual.normalized)
    if (not rule.get("allow_fraction_decimal_equivalence", False)
            and {actual.format_kind, expected_number.format_kind} == {"fraction", "decimal"}):
        return _confirmed("numeric", False, "format_rule_failed", rule,
                          normalized=actual.normalized)
    if (not rule.get("allow_equivalent_fraction", False)
            and actual.format_kind == expected_number.format_kind == "fraction"
            and actual.source != expected_number.source):
        return _confirmed("numeric", False, "format_rule_failed", rule,
                          normalized=actual.normalized)
    if rule.get("require_simplified_fraction", False) and actual.simplified_fraction is False:
        return _confirmed("numeric", False, "format_rule_failed", rule,
                          normalized=actual.normalized)
    return _confirmed("numeric", True, "equivalent_value", rule, normalized=actual.normalized)


def _grade_group(student_answer: Any, expected: Any, rule: dict[str, Any]) -> GradeResult:
    if not isinstance(expected, dict) or expected.get("kind") != "group" or not isinstance(expected.get("items"), list):
        return _pending("group", "invalid_rule_snapshot", rule)
    if not isinstance(student_answer, dict) or student_answer.get("kind") != "group" or not isinstance(student_answer.get("items"), list):
        return _pending("group", "invalid_group_shape", rule)
    actual_items = student_answer["items"]
    if any(not isinstance(item, dict) or not isinstance(item.get("item_id"), str) for item in actual_items):
        return _pending("group", "invalid_group_shape", rule)
    actual_by_id = {item["item_id"]: item.get("answer") for item in actual_items}
    if len(actual_by_id) != len(actual_items):
        return _pending("group", "invalid_group_shape", rule)
    expected_ids = [item.get("item_id") for item in expected["items"] if isinstance(item, dict)]
    if len(expected_ids) != len(expected["items"]) or set(actual_by_id) != set(expected_ids):
        return _pending("group", "invalid_group_shape", rule)

    results = []
    normalized_items = []
    for expected_item in expected["items"]:
        item_id = expected_item["item_id"]
        match_type = expected_item.get("answer_match_type")
        item_rule = {
            "answer_match_type": match_type,
            "answer_normalization_rule": rule,
        }
        answer = actual_by_id[item_id]
        expected_values = [expected_item.get("answer")]
        accepted_answers = expected_item.get("accepted_answers")
        if isinstance(accepted_answers, list):
            expected_values.extend(accepted_answers)
        unique_expected_values = []
        for expected_value in expected_values:
            if not any(
                expected_value == existing
                for existing in unique_expected_values
            ):
                unique_expected_values.append(expected_value)
        candidate_results = [
            grade_answer(
                answer, expected_value, item_rule,
                response_status="blank" if answer is None else "answered",
            )
            for expected_value in unique_expected_values
        ]
        result = next(
            (candidate for candidate in candidate_results if candidate.is_correct is True),
            candidate_results[0],
        )
        results.append({
            "item_id": item_id,
            "normalized_answer": result.normalized_answer,
            "grade_status": result.grade_status,
            "is_correct": result.is_correct,
            "reason_code": result.reason_code,
            "grading_details": result.details(),
        })
        normalized_items.append({"item_id": item_id, "answer": result.normalized_answer})

    item_results = tuple(results)
    normalized = {"kind": "group", "items": normalized_items}
    if any(item["is_correct"] is False for item in results):
        return _confirmed("group", False, "mismatched", rule, normalized=normalized,
                          item_results=item_results)
    if any(item["grade_status"] == "needs_review" for item in results):
        return _pending("group", "group_item_needs_review", rule, normalized=normalized,
                        item_results=item_results)
    return _confirmed("group", True, "matched", rule, normalized=normalized,
                      item_results=item_results)


def grade_answer(student_answer: Any, answer_snapshot: Any,
                 answer_rule_snapshot: dict[str, Any], *, response_status: str) -> GradeResult:
    match_type = answer_rule_snapshot.get("answer_match_type")
    raw_rule = answer_rule_snapshot.get("answer_normalization_rule", {})
    rule = raw_rule if isinstance(raw_rule, dict) else {}
    if match_type not in SUPPORTED_MATCH_TYPES:
        return _pending(str(match_type or "unknown"), "unsupported_match_type", rule)
    if response_status == "blank":
        return _confirmed(match_type, False, "blank_answer", rule, normalized=None)
    if response_status != "answered":
        return _pending(match_type, "invalid_answer_type", rule)
    if match_type == "choice":
        return _grade_choice(student_answer, answer_snapshot, rule)
    if match_type == "exact":
        return _grade_exact(student_answer, answer_snapshot, rule)
    if match_type == "latex_exact":
        return _grade_latex_exact(student_answer, answer_snapshot, rule)
    if match_type == "numeric":
        return _grade_numeric(student_answer, answer_snapshot, rule)
    return _grade_group(student_answer, answer_snapshot, rule)
