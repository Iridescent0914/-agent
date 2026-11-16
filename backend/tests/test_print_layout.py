from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.models import Assignment, PaperInstance, Student
from app.services.print_view import answer_subquestions, build_print_view, print_source_module


def test_answer_card_covers_every_reference_unit_in_both_chapters():
    root = Path(__file__).resolve().parents[2]
    for chapter in ("rational", "expr"):
        questions = json.loads((root / "data" / "interim" / f"{chapter}_all.json").read_text(encoding="utf-8"))
        for question in questions:
            reference = question["correct_answer"]
            before = deepcopy(reference)
            rows = answer_subquestions(reference, question["question_type"])
            actual = {
                blank.item_id: (row.subquestion_no, blank.blank_no)
                for row in rows for blank in row.blanks
            }
            assert actual == {
                part["item_id"]: (part["subquestion_no"], part["blank_no"])
                for part in reference["items"]
            }, question["original_qid"]
            assert sum(len(row.blanks) for row in rows) == len(reference["items"])
            assert reference == before
            for row in rows:
                for blank in row.blanks:
                    assert set(blank.model_dump()) == {"item_id", "blank_no", "kind"}


def test_legacy_reference_coordinates_keep_ids_and_sort_numerically():
    rows = answer_subquestions({"kind": "group", "items": [
        {"item_id": "sub_10.blank_2", "answer": "secret"},
        {"item_id": "sub_2.blank_1", "answer": "secret"},
        {"item_id": "sub_10.blank_1", "answer": "secret"},
    ]}, "fill_blank")
    assert [row.subquestion_no for row in rows] == [2, 10]
    assert [blank.item_id for blank in rows[1].blanks] == ["sub_10.blank_1", "sub_10.blank_2"]
    assert "secret" not in json.dumps([row.model_dump() for row in rows])
    legacy = answer_subquestions({"kind": "group", "items": [
        {"item_id": "2", "answer": "B"}, {"item_id": "1", "answer": "A"},
    ]}, "choice")
    assert [row.subquestion_no for row in legacy] == [1, 2]
    assert [row.blanks[0].item_id for row in legacy] == ["1", "2"]
    assert answer_subquestions("B", "choice")[0].blanks[0].item_id == "answer"


@pytest.mark.parametrize("parts", [
    [],
    [{"answer": "secret"}],
    [{"item_id": "x"}, {"item_id": "x"}],
    [{"item_id": "sub_1.blank_1", "subquestion_no": 2}],
    [{"item_id": "x", "subquestion_no": 1, "blank_no": 1},
     {"item_id": "y", "subquestion_no": 1, "blank_no": 1}],
])
def test_invalid_reference_does_not_silently_drop_or_invent_answer_spaces(parts):
    with pytest.raises(ValueError, match="参考答案"):
        answer_subquestions({"kind": "group", "items": parts}, "fill_blank")


def test_print_projection_uses_frozen_reference_and_source_without_leaking_answers():
    paper = SimpleNamespace(
        id=uuid4(), student_id=uuid4(), assignment_id=uuid4(), status="published",
        instance_code="RAT-TEST-0001", version_no=2, invalidated_at=None,
    )
    student = SimpleNamespace(student_code="S001")
    assignment = SimpleNamespace(title="打印布局", assignment_type="practice")
    items = [SimpleNamespace(
        id=uuid4(), order_no=index,
        question_snapshot={
            "question_text": "按题号作答", "question_type": "fill_blank", "assets": [],
            "correct_answer": "PRIVATE_ANSWER", "solution": "PRIVATE_SOLUTION",
        },
        options_snapshot=None,
        answer_snapshot={"kind": "group", "items": [
            {"item_id": "sub_3.blank_2", "subquestion_no": 3, "blank_no": 2,
             "answer": "PRIVATE_REFERENCE", "accepted_answers": ["PRIVATE_ALIAS"]},
            {"item_id": "sub_3.blank_1", "subquestion_no": 3, "blank_no": 1,
             "answer": "PRIVATE_REFERENCE"},
        ]},
        selection_audit={"source_module": module, "evidence": "PRIVATE_EVIDENCE"},
    ) for index, module in enumerate(("required", "history", "personalized"), start=1)]
    before = deepcopy(items)

    class FrozenPaperDB:
        def get(self, model, identity):
            # No current Question is consulted: editing today's answer key or
            # required flag must not change the historical paper's layout.
            return {PaperInstance: paper, Student: student, Assignment: assignment}[model]

        def scalars(self, statement):
            return items

    result = build_print_view(FrozenPaperDB(), paper.id)
    assert result.layout_version == "answer-card-v2"
    assert result.assignment_type == "practice"
    assert [slot.source_module for slot in result.answer_slots] == ["required", "history", "personalized"]
    assert [slot.order_no for slot in result.answer_slots] == [1, 2, 3]
    assert all(slot.blank_ids == ["sub_3.blank_1", "sub_3.blank_2"] for slot in result.answer_slots)
    assert "PRIVATE_" not in result.model_dump_json()
    assert [vars(item) for item in items] == [vars(item) for item in before]


def test_weekly_and_unknown_sources_are_not_mislabelled_as_required():
    assert print_source_module({"source_module": "weekly_summary"}) == "weekly_summary"
    assert print_source_module({"source_module": "unknown", "is_required": True}) == "unclassified"
    assert print_source_module(None) == "unclassified"
