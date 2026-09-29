from __future__ import annotations

import pytest

from app.services.grading import grade_answer


def rule(match_type: str, **normalization):
    return {
        "answer_match_type": match_type,
        "answer_normalization_rule": normalization,
    }


@pytest.mark.parametrize(
    ("answer", "expected", "correct"),
    [("B", "B", True), ("b", "B", True), ("Ｂ", "B", True), ("C", "B", False)],
)
def test_choice_normalization(answer, expected, correct):
    result = grade_answer(
        answer, expected,
        rule("choice", trim_whitespace=True, normalize_fullwidth_symbols=True, uppercase=True),
        response_status="answered",
    )
    assert result.grade_status == "auto_confirmed"
    assert result.is_correct is correct


def test_choice_invalid_format_needs_review_and_blank_is_wrong():
    invalid = grade_answer("选B", "B", rule("choice", uppercase=True), response_status="answered")
    assert invalid.grade_status == "needs_review" and invalid.is_correct is None
    blank = grade_answer(None, "B", rule("choice"), response_status="blank")
    assert blank.grade_status == "auto_confirmed" and blank.is_correct is False


def test_exact_only_applies_explicit_normalization():
    strict = grade_answer(" a+b ", "a+b", rule("exact"), response_status="answered")
    trimmed = grade_answer(
        " a+b ", "a+b", rule("exact", trim_whitespace=True), response_status="answered"
    )
    equivalent_but_different = grade_answer(
        "b+a=a+b", "a+b=b+a", rule("exact", trim_whitespace=True), response_status="answered"
    )
    assert strict.is_correct is False
    assert trimmed.is_correct is True
    assert equivalent_but_different.is_correct is False


def test_latex_exact_matches_canonical_source_and_defers_other_forms():
    matched = grade_answer(
        r"\frac{1}{2}", r"\frac{1}{2}",
        rule("latex_exact", trim_whitespace=True, latex_source_exact=True),
        response_status="answered",
    )
    variant = grade_answer(
        r"\dfrac{1}{2}", r"\frac{1}{2}",
        rule("latex_exact", trim_whitespace=True, latex_source_exact=True),
        response_status="answered",
    )
    assert matched.grade_status == "auto_confirmed" and matched.is_correct is True
    assert variant.grade_status == "needs_review" and variant.is_correct is None
    assert variant.reason_code == "formula_not_exact_match"


def test_numeric_equivalence_obeys_snapshot_rules():
    permissive = rule(
        "numeric", trim_whitespace=True, normalize_fullwidth_symbols=True,
        allow_fraction_decimal_equivalence=True, allow_equivalent_fraction=True,
        require_simplified_fraction=False, require_unit=False,
    )
    assert grade_answer("0.5", "1/2", permissive, response_status="answered").is_correct is True
    assert grade_answer("2/4", "1/2", permissive, response_status="answered").is_correct is True

    no_decimal = rule("numeric", allow_fraction_decimal_equivalence=False,
                      allow_equivalent_fraction=True)
    assert grade_answer("0.5", "1/2", no_decimal, response_status="answered").is_correct is False
    no_equivalent = rule("numeric", allow_fraction_decimal_equivalence=True,
                         allow_equivalent_fraction=False)
    assert grade_answer("2/4", "1/2", no_equivalent, response_status="answered").is_correct is False
    simplified = rule("numeric", allow_fraction_decimal_equivalence=True,
                      allow_equivalent_fraction=True, require_simplified_fraction=True)
    assert grade_answer("2/4", "1/2", simplified, response_status="answered").is_correct is False


@pytest.mark.parametrize(
    "answer",
    ["1/0", "__import__('os')", "2 ** 999", "x + 1", "(" * 30 + "1" + ")" * 30],
)
def test_numeric_failures_never_execute_and_need_review(answer):
    result = grade_answer(answer, "1", rule("numeric"), response_status="answered")
    assert result.grade_status == "needs_review"
    assert result.is_correct is None


def test_group_aligns_by_item_id_and_preserves_item_results():
    expected = {
        "kind": "group",
        "items": [
            {"item_id": "1", "answer": "B", "answer_match_type": "choice"},
            {"item_id": "2", "answer": "1/2", "answer_match_type": "numeric"},
        ],
    }
    actual = {
        "kind": "group",
        "items": [
            {"item_id": "2", "answer": "2/4"},
            {"item_id": "1", "answer": "b"},
        ],
    }
    result = grade_answer(
        actual, expected,
        rule("group", uppercase=True, allow_equivalent_fraction=True),
        response_status="answered",
    )
    assert result.is_correct is True
    assert [item["item_id"] for item in result.item_results] == ["1", "2"]


def test_group_with_wrong_and_unreadable_item_is_wrong_without_losing_details():
    expected = {
        "kind": "group",
        "items": [
            {"item_id": "1", "answer": "A", "answer_match_type": "choice"},
            {"item_id": "2", "answer": "1", "answer_match_type": "numeric"},
        ],
    }
    actual = {
        "kind": "group",
        "items": [
            {"item_id": "1", "answer": "B"},
            {"item_id": "2", "answer": "not-a-number"},
        ],
    }
    result = grade_answer(actual, expected, rule("group"), response_status="answered")
    assert result.grade_status == "auto_confirmed"
    assert result.is_correct is False
    assert len(result.details()["item_results"]) == 2
