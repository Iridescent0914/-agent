from app.services.answer_units import (
    ANSWER_SCHEMA_VERSION,
    is_canonical_answer_structure,
    normalize_answer_structure,
)


def normalize(question_type, text, answer, match_type="group", reference=""):
    return normalize_answer_structure(
        question_type=question_type,
        question_text=text,
        correct_answer=answer,
        answer_match_type=match_type,
        reference_answer_raw=reference,
    )


def test_multiple_blanks_inside_subquestion_are_split_into_leaf_units():
    result = normalize(
        "fill_blank",
        "（1）甲____。（2）乙____，丙____。",
        {
            "kind": "group",
            "items": [
                {"item_id": "1", "answer": "A", "answer_match_type": "exact"},
                {
                    "item_id": "2",
                    "answer": ["B", "C"],
                    "answer_match_type": "exact",
                },
            ],
        },
    )
    assert result.answer["schema_version"] == ANSWER_SCHEMA_VERSION
    assert [item["item_id"] for item in result.answer["items"]] == [
        "sub_1.blank_1",
        "sub_2.blank_1",
        "sub_2.blank_2",
    ]
    assert [item["answer"] for item in result.answer["items"]] == ["A", "B", "C"]
    assert result.split_item_count == 1
    assert is_canonical_answer_structure(result.answer)
    assert normalize(
        "fill_blank",
        "（1）甲____。（2）乙____，丙____。",
        result.answer,
    ).answer == result.answer


def test_alternative_answers_do_not_create_extra_blanks():
    result = normalize(
        "fill_blank",
        "一个数与零相加仍得____。",
        {
            "kind": "group",
            "items": [{
                "item_id": "blank_1",
                "answer": ["这个数", "原数"],
                "answer_match_type": "set",
            }],
        },
    )
    assert len(result.answer["items"]) == 1
    assert result.answer["items"][0]["answer"] == "这个数"
    assert result.answer["items"][0]["accepted_answers"] == ["这个数", "原数"]
    assert result.accepted_answer_item_count == 1

    repeated = normalize(
        "fill_blank",
        "一个数与零相加仍得____。",
        result.answer,
    )
    assert repeated.answer == result.answer


def test_scalar_answer_keeps_its_numeric_match_type():
    result = normalize("fill_blank", "计算：____。", "2", "numeric", "2")
    item = result.answer["items"][0]
    assert item["item_id"] == "sub_1.blank_1"
    assert item["answer"] == "2"
    assert item["answer_match_type"] == "numeric"
    assert result.unresolved_item_count == 0


def test_delimited_legacy_subquestion_answer_is_split_by_known_blank_count():
    result = normalize(
        "fill_blank",
        "（1）依次填____、____、____。",
        {
            "kind": "group",
            "items": [{
                "item_id": "1",
                "answer": "甲，乙，丙",
                "answer_match_type": "exact",
            }],
        },
    )
    assert [item["answer"] for item in result.answer["items"]] == [
        "甲", "乙", "丙"
    ]
    assert [item["item_id"] for item in result.answer["items"]] == [
        "sub_1.blank_1", "sub_1.blank_2", "sub_1.blank_3"
    ]


def test_missing_legacy_subquestions_are_kept_as_reviewable_slots():
    result = normalize(
        "fill_blank",
        "（1）甲____。（2）乙____。（3）丙____。",
        {
            "kind": "group",
            "items": [{
                "item_id": "2",
                "answer": "B",
                "answer_match_type": "exact",
            }],
        },
        reference="A；（2）B",
    )
    assert [item["answer"] for item in result.answer["items"]] == [
        "A", "B", None
    ]
    assert result.unresolved_item_count == 1


def test_numbered_statements_in_scalar_choice_are_not_answer_slots():
    result = normalize(
        "choice",
        "下列说法正确的个数是（ ）。（1）甲；（2）乙；（3）丙。A.1 B.2",
        "A",
        "choice",
        "A",
    )
    assert [item["item_id"] for item in result.answer["items"]] == [
        "sub_1.blank_1"
    ]
    assert result.unresolved_item_count == 0


def test_manual_subquestions_split_when_reference_markers_are_unambiguous():
    result = normalize(
        "solution",
        "（1）求甲；\n（2）说明乙。",
        None,
        None,
        "（1）甲的解答。\n（2）乙的理由。",
    )
    assert [item["answer"] for item in result.answer["items"]] == [
        "甲的解答",
        "乙的理由",
    ]
    assert all(
        item["answer_match_type"] == "manual"
        for item in result.answer["items"]
    )
    assert result.manual_item_count == 2
    assert result.unresolved_item_count == 0


def test_ambiguous_manual_reference_creates_reviewable_null_units():
    result = normalize(
        "calculation",
        "（1）计算甲；\n（2）计算乙。",
        None,
        None,
        "答案排版无法可靠对应。",
    )
    assert [item["answer"] for item in result.answer["items"]] == [None, None]
    assert result.unresolved_item_count == 2
