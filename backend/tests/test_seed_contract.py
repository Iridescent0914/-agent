from __future__ import annotations

import json
import re
from pathlib import Path


CODE_ROOT = Path(__file__).resolve().parents[2]
QUESTIONS_PATH = CODE_ROOT / "data" / "seed" / "rational_questions.json"
MANIFEST_PATH = CODE_ROOT / "data" / "web" / "rational" / "asset_manifest.json"
ASSET_PATTERN = re.compile(r"\[ASSET:([^\]]+)\]")


def test_m0_seed_contract_is_preserved() -> None:
    questions = json.loads(QUESTIONS_PATH.read_text(encoding="utf-8"))
    assert len(questions) == 45
    assert len({item["original_qid"] for item in questions}) == 45
    assert sum(item["question_type"] == "choice" for item in questions) == 11
    assert sum(item["question_type"] == "fill_blank" for item in questions) == 32
    assert sum(item["question_type"] == "true_false" for item in questions) == 2
    assert all(item["grading_readiness"] == "machine_ready" for item in questions)
    assert all(item["source_review_status"] == "reviewed" for item in questions)
    assert all(item["correct_answer"] is not None for item in questions)
    assert all(item["grade"] == "六年级上册" for item in questions)
    assert all(item["chapter"]["name"] == "第1章 有理数" for item in questions)
    assert all(item["section"] for item in questions)
    assert all(item["main_exam_point"]["name"] for item in questions)
    assert all(item["core_literacy"] for item in questions)
    assert all(type(item["is_required"]) is bool for item in questions)
    by_qid = {item["original_qid"]: item for item in questions}
    assert [part["answer"] for part in by_qid["q.math.g6.rational.school.050"]["correct_answer"]["items"]] == ["D", "B"]
    assert [part["answer"] for part in by_qid["q.math.g6.rational.school.097"]["correct_answer"]["items"]] == ["正确", "正确"]
    assert [part["answer"] for part in by_qid["q.math.g6.rational.school.122"]["correct_answer"]["items"]] == ["错误", "正确", "正确", "错误"]


def test_all_referenced_assets_were_converted() -> None:
    questions = json.loads(QUESTIONS_PATH.read_text(encoding="utf-8"))
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    referenced = {
        path
        for question in questions
        for path in ASSET_PATTERN.findall(question["question_text"])
    }
    entries = {item["source_path"]: item for item in manifest["assets"]}
    assert referenced
    assert referenced <= entries.keys()
    for source_path in referenced:
        entry = entries[source_path]
        assert entry["status"] == "converted"
        output = CODE_ROOT / "data" / "web" / entry["web_path"]
        assert output.exists()
        assert output.stat().st_size > 0
