from __future__ import annotations

from copy import deepcopy
from fractions import Fraction
import json
from pathlib import Path
import sys
import re
from xml.etree import ElementTree as ET

import pytest

from app.services.answer_units import normalize_answer_structure
from app.services.grading.engine import grade_answer
from app.services.question_pool_corrections import apply_pool_corrections
from app.services.question_pool_quality import pool_answer_issues

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "data/config/question_pool_corrections.json"


def catalog():
    return [record for chapter in ("rational", "expr") for record in json.loads((ROOT / f"data/interim/{chapter}_all.json").read_text(encoding="utf-8"))]


def test_all_ready_answers_and_aliases_can_actually_be_graded():
    for record in catalog():
        if record["grading_readiness"] != "machine_ready":
            continue
        assert pool_answer_issues(record["correct_answer"], record["answer_match_type"], record["answer_normalization_rule"]) == [], record["original_qid"]
        for item in record["correct_answer"]["items"]:
            for answer in item.get("accepted_answers", [item["answer"]]):
                actual = deepcopy(record["correct_answer"])
                next(part for part in actual["items"] if part["item_id"] == item["item_id"])["answer"] = answer
                result = grade_answer(actual, record["correct_answer"], {"answer_match_type": record["answer_match_type"], "answer_normalization_rule": record["answer_normalization_rule"]}, response_status="answered")
                assert result.is_correct is True, (record["original_qid"], item["item_id"], answer)


def test_source_uncertainty_does_not_enter_pool():
    records = {record["original_qid"]: record for record in catalog()}
    expected = {"q.math.g6.rational.school.070", "q.math.g6.expr.school.013", "q.math.g6.expr.school.032"}
    pending = {qid for qid, record in records.items() if record["question_type"] in {"choice", "fill_blank", "true_false"} and record["grading_readiness"] != "machine_ready"}
    assert pending == expected
    for qid in expected:
        assert records[qid]["pool_governance"]["blocked_reasons"]


def test_corrections_are_idempotent_and_do_not_change_other_question_types():
    records = catalog()
    before = deepcopy(records)
    apply_pool_corrections(records, CONFIG)
    assert records == before
    assert all(record == original for record, original in zip(records, before) if record["question_type"] in {"calculation", "solution"})


def test_curated_answer_units_do_not_gain_phantom_blanks_when_normalized():
    for record in catalog():
        if record.get("pool_governance") is None or record["grading_readiness"] != "machine_ready":
            continue
        normalized = normalize_answer_structure(question_type=record["question_type"], question_text=record["question_text"], correct_answer=record["correct_answer"], answer_match_type=record["answer_match_type"], reference_answer_raw=record["reference_answer_raw"])
        assert normalized.answer == record["correct_answer"], record["original_qid"]


def test_rational_080_image_answers_are_complete_and_grade_final_results():
    record = next(record for record in catalog() if record["original_qid"] == "q.math.g6.rational.school.080")
    items = record["correct_answer"]["items"]
    assert record["question_type"] == "calculation"
    assert record["grading_readiness"] == "raw_answer_only"
    assert "pool_profile_type_unsupported" in record["pool_governance"]["blocked_reasons"]
    assert [item["item_id"] for item in items] == ["sub_1.blank_1", "sub_2.blank_1", "sub_3.blank_1"]
    assert all(item["answer_match_type"] == "numeric" and item["answer"] and "[ASSET:" not in item["answer"] for item in items)
    assert items[0]["answer"] == "2023/2024"
    assert items[2]["answer"] == "4048/2025"
    assert record["answer_normalization_rule"]["final_answer_only"] is True
    assert "answer_units_need_review" not in record["import_issues"]
    assert sum((Fraction(1, k * (k + 1)) for k in range(1, 2024)), Fraction()) == Fraction(2023, 2024)
    assert sum((Fraction(2, k * (k + 1)) for k in range(1, 2025)), Fraction()) == Fraction(4048, 2025)
    for n in (1, 2, 10, 2024):
        assert sum((Fraction(1, k * (k + 2)) for k in range(1, n + 1)), Fraction()) == Fraction(3, 4) - Fraction(1, 2 * (n + 1)) - Fraction(1, 2 * (n + 2))
    normalized = normalize_answer_structure(question_type=record["question_type"], question_text=record["question_text"], correct_answer=record["correct_answer"], answer_match_type=record["answer_match_type"], reference_answer_raw=record["reference_answer_raw"])
    assert normalized.answer == record["correct_answer"]


@pytest.mark.parametrize("actual", ["3/4-1/(2(n+1))-1/(2(n+2))", "n(3n+5)/(4(n+1)(n+2))", "(3n^2+5n)/(4n^2+12n+8)"])
def test_rational_expression_equivalent_forms_auto_confirm(actual):
    rule = {"answer_match_type": "numeric", "answer_normalization_rule": {"symbolic_variable": "n", "symbolic_domain": "positive_integer"}}
    result = grade_answer(actual, "3/4-1/(2*(n+1))-1/(2*(n+2))", rule, response_status="answered")
    assert result.grade_status == "auto_confirmed"
    assert result.is_correct is True


@pytest.mark.parametrize("actual", ["n/(n+1)", "3/4", "n(n+3)/(2(n+1)(n+2))"])
def test_rational_expression_wrong_forms_are_not_accepted(actual):
    result = grade_answer(actual, "3/4-1/(2*(n+1))-1/(2*(n+2))", {"answer_match_type": "numeric", "answer_normalization_rule": {"symbolic_variable": "n", "symbolic_domain": "positive_integer"}}, response_status="answered")
    assert result.is_correct is False


@pytest.mark.parametrize("actual", ["__import__('os')", "sin(n)", "1/(n-1)", "1/0", "n^999999", "n.__class__", "1 2"])
def test_rational_expression_unsupported_or_unsafe_forms_require_review(actual):
    result = grade_answer(actual, "1", {"answer_match_type": "numeric", "answer_normalization_rule": {"symbolic_variable": "n", "symbolic_domain": "positive_integer"}}, response_status="answered")
    assert result.grade_status == "needs_review"
    assert result.is_correct is None


def test_known_source_math_errors_and_slot_alignment_are_corrected():
    records = {record["original_qid"]: record for record in catalog()}
    def values(qid):
        return [item["answer"] for item in records[qid]["correct_answer"]["items"]]
    assert values("q.math.g6.rational.school.016") == ["-2", "1", "3", "0或4"]
    assert values("q.math.g6.rational.school.030")[-1] == "2"
    assert values("q.math.g6.rational.school.036")[3] == "-30"
    assert values("q.math.g6.rational.school.036")[7] == "-1/56"
    assert values("q.math.g6.rational.school.106")[-1] == "10000"
    assert values("q.math.g6.expr.school.028")[-5:] == ["-a^2+7a-3", "3x+4y-3", "-11", "②④", "6x-5"]
    assert "(3)/(2) b" in records["q.math.g6.expr.school.035"]["question_text"]
    assert "a_(2024)" in records["q.math.g6.expr.school.034"]["question_text"]


def test_formula_mismatch_still_requires_review_instead_of_guessing_equivalence():
    record = next(record for record in catalog() if record["original_qid"] == "q.math.g6.expr.school.017")
    answer = deepcopy(record["correct_answer"])
    answer["items"][0]["answer"] = "任意自然语言解释"
    result = grade_answer(answer, record["correct_answer"], {"answer_match_type": "group", "answer_normalization_rule": record["answer_normalization_rule"]}, response_status="answered")
    assert result.grade_status == "needs_review"
    assert result.is_correct is None


def test_every_ready_question_body_asset_decodes_as_an_image():
    from PIL import Image
    manifests = {entry["source_path"]: entry for chapter in ("rational", "expr")
                 for entry in json.loads((ROOT / f"data/web/{chapter}/asset_manifest.json").read_text(encoding="utf-8"))["assets"]}
    checked = set()
    for record in catalog():
        if record["grading_readiness"] != "machine_ready":
            continue
        references = set(re.findall(r"\[ASSET:([^\]]+)\]", record["question_text"]))
        assert references <= {asset["path"] for asset in record["assets"]}, record["original_qid"]
        for reference in references - checked:
            entry = manifests[reference]
            assert entry["status"] == "converted"
            target = ROOT / "data/web" / entry["web_path"]
            if target.suffix == ".svg":
                assert ET.parse(target).getroot().tag == "{http://www.w3.org/2000/svg}svg"
            else:
                with Image.open(target) as source:
                    source.verify()
            checked.add(reference)
    assert checked


def test_legacy_word_fields_and_split_scripts_are_preserved():
    sys.path.insert(0, str(ROOT / "scripts"))
    from build_rational_m0 import element_text, W_NS
    paragraph = ET.fromstring(f'''<w:p xmlns:w="{W_NS}">
      <w:r><w:t>a</w:t></w:r>
      <w:r><w:rPr><w:vertAlign w:val="superscript"/></w:rPr><w:t>202</w:t></w:r>
      <w:r><w:rPr><w:vertAlign w:val="superscript"/></w:rPr><w:t>3</w:t></w:r>
      <w:r><w:fldChar w:fldCharType="begin"/></w:r>
      <w:r><w:instrText> EQ \\F(3,</w:instrText></w:r>
      <w:r><w:instrText>2)</w:instrText></w:r>
      <w:r><w:fldChar w:fldCharType="end"/></w:r>
      </w:p>''')
    assert element_text(paragraph) == "a^(2023)(3)/(2)"


def test_expr036_native_canvas_and_four_independent_choice_items_are_ready():
    from app.services.assets import question_asset_exists, resolve_assets
    record = next(row for row in catalog() if row["original_qid"] == "q.math.g6.expr.school.036")
    path = "assets/expr/q.math.g6.expr.school.036_canvas265.svg"
    assert record["question_text"].count(f"[ASSET:{path}]") == 1
    assert "[ASSET:unknown]" not in record["question_text"]
    assert record["grading_readiness"] == "machine_ready"
    assert re.findall(r"[（(]([1-4])[）)]", record["question_text"]) == ["1", "2", "3", "4"]
    assert [item["answer"] for item in record["correct_answer"]["items"]] == ["C", "A", "C", "D"]
    assert [item["item_id"] for item in record["correct_answer"]["items"]] == [f"sub_{i}.blank_1" for i in range(1,5)]
    assert [item["subquestion_no"] for item in record["correct_answer"]["items"]] == [1,2,3,4]
    assert [item["blank_no"] for item in record["correct_answer"]["items"]] == [1,1,1,1]
    assert "第4小题第1空：D" in record["reference_answer_raw"]
    actual = deepcopy(record["correct_answer"])
    actual["items"][2]["answer"] = "A"
    result = grade_answer(actual, record["correct_answer"], {"answer_match_type": "group", "answer_normalization_rule": record["answer_normalization_rule"]}, response_status="answered")
    assert result.is_correct is False
    assert [item["is_correct"] for item in result.item_results] == [True,True,False,True]
    assert not {"asset_relationship_missing", "pool_missing_source_content"} & set(record["import_issues"])
    assert question_asset_exists(path)
    resolved = next(asset for asset in resolve_assets(record["assets"]) if asset["path"] == path)
    assert resolved["conversion_status"] == "converted" and resolved["web_url"].endswith("_canvas265.svg")
    svg = ET.fromstring((ROOT/"data"/path).read_text(encoding="utf-8"))
    ns = {"s": "http://www.w3.org/2000/svg"}
    texts = svg.findall("s:text", ns)
    assert [node.text for node in texts[:16]] == ["2", "1", "3", "0", "6", "5", "7", "4", "10", "9", "11", "8", "14", "13", "15", "12"]
    assert len(svg.findall("s:rect[@fill='none']", ns)) == 4
    assert all(10 <= float(node.get("font-size")) <= 30 for node in texts)


@pytest.mark.parametrize("value", ["−0.81", "–0.81", "－0.81"])
def test_unicode_negative_numeric_answer_is_accepted(value):
    result = grade_answer(value, "-0.81", {"answer_match_type": "numeric", "answer_normalization_rule": {"normalize_fullwidth_symbols": True}}, response_status="answered")
    assert result.is_correct is True
