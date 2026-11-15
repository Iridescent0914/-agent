from __future__ import annotations

import json
from io import BytesIO
from types import SimpleNamespace

import httpx
import pytest
from pypdf import PdfWriter
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.database import engine
from app.services.question_import import ImportProblem, add_candidate, create_batch
from app.services.question_import_s2 import (
    COMBINED_PROMPT_VERSION, QwenPdfProvider, candidate_from_question,
    parse_model_output, plan_units,
)
from app.services.question_import_s2_worker import start_s2
from app.services.question_import_s3 import reconcile_source_answers
import app.services.question_import_s3 as s3_service
import app.services.question_import_s2 as s2_service
import app.services.question_import_s2_worker as s2_worker


def test_overlap_duplicate_answers_are_not_pending_claims():
    numbered = [_answer("6", ">"), _answer("11", "$3.5\\times10^{6}$个"),
                _answer("16", "-21")]
    repeated = [_answer(None, ">"), _answer(None, "$3.5 \\times 10^{6}$个"),
                _answer(None, "-21")]
    for index, answer in enumerate(numbered):
        answer["page_start"] = 4 + index * 2
        repeated[index]["page_start"] = answer["page_start"] + 1
    units = [{"source_answers": [numbered[0]]},
             {"source_answers": [repeated[0], numbered[1]]},
             {"source_answers": [repeated[1], numbered[2]]},
             {"source_answers": [repeated[2]]}]
    items = [SimpleNamespace(payload={"source": {"original_number": answer["original_number"]},
                                      "answers": [{"answer_source": "source_extracted",
                                                   "text": answer["text"]}]}) for answer in numbered]
    result = s3_service._classify_overlap_duplicates(
        {"units": units}, {"unassigned_answers": [
            {**answer, "reason": "missing_question_number"} for answer in repeated]}, items)
    assert result["schema_version"] == "s3-source-answer-reconciliation-v3"
    assert not result["unassigned_answers"]
    assert [row["matched_original_number"] for row in result["overlap_duplicate_answers"]] == ["6", "11", "16"]
    assert all(answer["original_number"] is None for answer in repeated)


@pytest.fixture
def db():
    with engine.connect() as connection:
        transaction = connection.begin()
        session = Session(bind=connection, expire_on_commit=False,
                          join_transaction_mode="create_savepoint")
        try:
            yield session
        finally:
            session.close()
            transaction.rollback()


def _pdf(pages: int = 1) -> bytes:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=612, height=792)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def _question(number: str) -> dict:
    return {"original_number": number, "question_text": f"题目 {number}",
            "options": [], "subquestions": [], "math_fragments": [],
            "diagram_clues": [], "page_start": 1, "page_end": 1,
            "uncertain_fragments": []}


def _classified_question(number: str, *, classification: str = "independent_question") -> dict:
    return {**_question(number), "source_classification": classification,
            "classification_evidence": "题号连续，后接待作答题干"}


def _answer(number: str | None, text: str) -> dict:
    return {"source_file_id": "source-file", "original_number": number,
            "subquestion_number": None, "text": text,
            "page_start": 1, "page_end": 1, "uncertain_fragments": []}


def test_page_heading_after_question_marks_page_mixed(monkeypatch):
    page_texts = [
        "1．求值：1+1\n参考答案与试题解析\n【答案】2",
        "2．【答案】3",
        "【点评】倒数\n考点卡片\n倒数定义",
        "倒数定义续页",
    ]
    pages = [SimpleNamespace(extract_text=lambda value=value: value) for value in page_texts]
    monkeypatch.setattr(s2_service, "PdfReader", lambda *_args, **_kwargs: SimpleNamespace(pages=pages))
    assert s2_service.pdf_page_roles(b"not-a-real-pdf") == ["mixed", "answer", "mixed", "reference"]


def test_combined_pdf_one_pass_returns_separate_source_answers():
    captured = {}

    def respond(request: httpx.Request):
        captured.update(json.loads(request.content))
        return httpx.Response(200, json={
            "id": "combined-1", "choices": [{"finish_reason": "stop", "message": {
                "content": json.dumps({"complete": True, "questions": [_classified_question("3")],
                                       "nonquestion_fragments": [{"original_number": "3", "excerpt": "题目 3",
                                                                  "page": 1, "source_classification": "answer_restatement",
                                                                  "classification_evidence": "后接【答案】7"}],
                                       "source_answers": [{"original_number": "3",
                                                           "subquestion_number": None,
                                                           "answer_text": "7", "page_start": 1,
                                                           "page_end": 1,
                                                           "uncertain_fragments": []}],
                                       "unresolved_fragments": []})}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 50,
                      "x_tools": {"pdf_page_parser": {"count": 1}}},
        })

    settings = Settings(question_import_s2_api_key="test-only",
                        question_import_s2_base_url="https://example.invalid/compatible-mode/v1")
    output, meta, _ = QwenPdfProvider(settings=settings, transport=httpx.MockTransport(respond)).extract(
        _pdf(), page_count=1, include_answers=True)
    assert output.questions[0].question_text == "题目 3"
    assert output.source_answers[0].answer_text == "7"
    assert output.nonquestion_fragments[0].source_classification == "answer_restatement"
    assert meta["prompt_version"] == COMBINED_PROMPT_VERSION
    assert len([part for part in captured["messages"][0]["content"] if part["type"] == "file"]) == 1
    assert "source_answers" in captured["response_format"]["json_schema"]["schema"]["required"]
    question_schema = captured["response_format"]["json_schema"]["schema"]["$defs"]["ClassifiedModelQuestion"]
    assert {"source_classification", "classification_evidence"} <= set(question_schema["required"])
    assert "nonquestion_fragments" in captured["response_format"]["json_schema"]["schema"]["required"]
    assert "不要把解题步骤" in captured["messages"][0]["content"][1]["text"]
    assert "不要根据固定标题名称或整页位置" in captured["messages"][0]["content"][1]["text"]
    assert "complete 仅表示" in captured["messages"][0]["content"][1]["text"]
    with pytest.raises(ImportProblem) as missing_classification:
        parse_model_output({"complete": True, "questions": [_question("3")],
                            "source_answers": [], "nonquestion_fragments": []},
                           plan_units(1, pages_per_unit=1, overlap=0)[0], include_answers=True)
    assert missing_classification.value.code == "INVALID_MODEL_OUTPUT"
    with pytest.raises(ImportProblem) as error:
        parse_model_output({"complete": True, "questions": [], "source_answers": [
            {"original_number": "3", "subquestion_number": None,
             "answer_text": "7", "page_start": 2, "page_end": 2,
            "uncertain_fragments": []}], "nonquestion_fragments": [], "unresolved_fragments": []},
            plan_units(1, pages_per_unit=1, overlap=0)[0], include_answers=True)
    assert error.value.code == "INVALID_MODEL_PAGE"


def test_combined_answers_link_only_unique_unedited_question(db, tmp_path, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "question_import_dir", tmp_path / "imports")
    monkeypatch.setattr(settings, "question_import_s2_enabled", True)
    monkeypatch.setattr(settings, "question_import_s2_api_key", "test-only")
    monkeypatch.setattr(settings, "question_import_s2_operator_token", "operator-test")
    monkeypatch.setattr(settings, "question_import_s2_base_url", "https://example.invalid/compatible-mode/v1")
    batch = create_batch(db, source_mode="combined", files=[("questions", "combined.pdf", _pdf())])
    batch.stage = "awaiting_s2"
    start_s2(db, batch.id, budget_cny=2, operator_token="operator-test")
    assert batch.checkpoint["s2"]["prompt_version"] == COMBINED_PROMPT_VERSION
    source_id = batch.files[0]["file_id"]
    unit = plan_units(1, pages_per_unit=1, overlap=0)[0]
    for number in ("3", "3", "4", "5"):
        question = parse_model_output({"complete": True, "questions": [_question(number)],
                                       "unresolved_fragments": []}, unit).questions[0]
        add_candidate(db, batch, candidate_from_question(source_id, question, unit))
    answers = [_answer("3", "错误归属风险"), _answer("4", "7"),
               _answer("5", "8"), _answer("5", "9"), _answer(None, "缺题号")]
    batch.status = "review_pending"
    batch.stage = "s2_completed"
    batch.checkpoint = {"s2": {**batch.checkpoint["s2"], "units": [{"source_answers": answers}]}}
    db.flush()

    result = reconcile_source_answers(db, batch.id)
    assert result["source_answer_count"] == 5
    assert result["linked_answer_count"] == 1
    assert {row["reason"] for row in result["unassigned_answers"]} == {
        "repeated_question_number", "missing_question_number", "repeated_answer_position"}
    from app.models import QuestionImportItem
    from sqlalchemy import select
    items = list(db.scalars(select(QuestionImportItem).where(
        QuestionImportItem.batch_id == batch.id).order_by(QuestionImportItem.ordinal)))
    assert not items[0].payload["answers"] and not items[1].payload["answers"]
    linked = items[2].payload
    assert linked["answers"][0]["answer_source"] == "source_extracted"
    assert linked["answers"][0]["teacher_confirmed"] is False
    assert linked["selected_answer_id"] is None
    assert linked["teacher_decision"] == "pending"
    assert reconcile_source_answers(db, batch.id) == result
    assert items[2].revision == 2


def test_combined_worker_keeps_answers_from_same_paid_unit(db, tmp_path, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "question_import_dir", tmp_path / "imports")
    monkeypatch.setattr(settings, "question_import_s2_enabled", True)
    monkeypatch.setattr(settings, "question_import_s2_api_key", "test-only")
    monkeypatch.setattr(settings, "question_import_s2_operator_token", "operator-test")
    monkeypatch.setattr(settings, "question_import_s2_base_url", "https://example.invalid/compatible-mode/v1")
    batch = create_batch(db, source_mode="combined", files=[("questions", "combined.pdf", _pdf(2))])
    batch.stage = "awaiting_s2"
    start_s2(db, batch.id, budget_cny=2, operator_token="operator-test")
    db.commit()
    connection = db.get_bind()

    def session_factory():
        return Session(bind=connection, expire_on_commit=False,
                       join_transaction_mode="create_savepoint")

    monkeypatch.setattr(s2_worker, "SessionLocal", session_factory)
    monkeypatch.setattr(s2_worker, "prepare_pdf", lambda *_: (tmp_path / "unused.pdf", 2, []))
    monkeypatch.setattr(s2_worker, "pdf_unit_bytes", lambda *_: _pdf(2))
    monkeypatch.setattr(s2_worker, "pdf_page_roles", lambda *_: pytest.fail("v4 不应按标题划分页角色"))

    class MockProvider:
        def extract(self, data, *, page_count, core_page_count, capture_response=None,
                    include_answers=False, page_roles=None):
            assert include_answers is True
            assert page_count == core_page_count == 2
            assert page_roles is None
            uncertain_question = {**_classified_question("4", classification="uncertain"),
                                  "page_start": 2, "page_end": 2}
            output = parse_model_output({"complete": True,
                                         "questions": [_classified_question("3"), uncertain_question],
                                         "nonquestion_fragments": [{"original_number": "3", "excerpt": "题目 3",
                                                                    "page": 2, "source_classification": "answer_restatement",
                                                                    "classification_evidence": "重述后紧跟答案 7"}],
                                         "source_answers": [{"original_number": "3",
                                                             "subquestion_number": None,
                                                             "answer_text": "7", "page_start": 2,
                                                             "page_end": 2,
                                                             "uncertain_fragments": []}],
                                         "unresolved_fragments": []},
                                        plan_units(2, pages_per_unit=2, overlap=0)[0],
                                        include_answers=True)
            return output, {"usage": {"prompt_tokens": 100, "completion_tokens": 50,
                                      "x_tools": {"pdf_page_parser": {"count": 1}}},
                            "elapsed_seconds": 1.0, "response_id": "combined-worker-1"}, 1.0

    monkeypatch.setattr(s2_worker, "QwenPdfProvider", MockProvider)
    assert s2_worker.process_s2_one() is True
    db.refresh(batch)
    assert batch.stage == "s2_completed", batch.error_message
    unit = batch.checkpoint["s2"]["units"][0]
    assert unit["candidate_count"] == 2
    assert unit["ignored_nonquestion_sections"][0]["section"] == "answer_restatement"
    assert unit["ignored_nonquestion_sections"][0]["classification_evidence"] == "重述后紧跟答案 7"
    assert unit["source_answers"][0]["text"] == "7"
    assert unit["source_answers"][0]["source_file_id"] == batch.files[0]["file_id"]


def test_combined_worker_reads_reference_looking_pages_with_model(db, tmp_path, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "question_import_dir", tmp_path / "imports")
    monkeypatch.setattr(settings, "question_import_s2_enabled", True)
    monkeypatch.setattr(settings, "question_import_s2_api_key", "test-only")
    monkeypatch.setattr(settings, "question_import_s2_operator_token", "operator-test")
    monkeypatch.setattr(settings, "question_import_s2_base_url", "https://example.invalid/compatible-mode/v1")
    batch = create_batch(db, source_mode="combined", files=[("questions", "reference.pdf", _pdf(2))])
    batch.stage = "awaiting_s2"
    start_s2(db, batch.id, budget_cny=2, operator_token="operator-test")
    db.commit()
    connection = db.get_bind()

    def session_factory():
        return Session(bind=connection, expire_on_commit=False,
                       join_transaction_mode="create_savepoint")

    monkeypatch.setattr(s2_worker, "SessionLocal", session_factory)
    monkeypatch.setattr(s2_worker, "prepare_pdf", lambda *_: (tmp_path / "unused.pdf", 2, []))
    monkeypatch.setattr(s2_worker, "pdf_unit_bytes", lambda *_: _pdf(2))
    monkeypatch.setattr(s2_worker, "pdf_page_roles", lambda *_: pytest.fail("v4 不能按标题跳过模型"))

    class MockProvider:
        def extract(self, *_args, **_kwargs):
            output = parse_model_output({"complete": True, "questions": [_classified_question("9")],
                                         "source_answers": [], "nonquestion_fragments": [],
                                         "unresolved_fragments": []},
                                        plan_units(2, pages_per_unit=2, overlap=0)[0], include_answers=True)
            return output, {"usage": {"prompt_tokens": 100, "completion_tokens": 50},
                            "elapsed_seconds": 1.0, "response_id": "reference-worker-1"}, 1.0

    monkeypatch.setattr(s2_worker, "QwenPdfProvider", MockProvider)
    assert s2_worker.process_s2_one() is True
    db.refresh(batch)
    assert batch.stage == "s2_completed", batch.error_message
    assert batch.checkpoint["s2"]["calls"] == 1
    assert batch.checkpoint["s2"]["units"][0].get("skipped_reference") is False
    assert batch.checkpoint["s2"]["units"][0]["candidate_count"] == 1
    assert not batch.checkpoint["s2"]["units"][0]["unresolved_fragments"]


def test_combined_output_truncation_splits_core_before_retry(tmp_path, monkeypatch):
    monkeypatch.setattr(s2_worker, "_result_path", lambda *_: tmp_path / "not-saved.json")
    checkpoint = {"page_count": 4, "units": [
        {"index": 0, "first_page": 1, "last_page": 3,
         "core_first_page": 1, "core_last_page": 2, "status": "pending"},
        {"index": 1, "first_page": 3, "last_page": 4,
         "core_first_page": 3, "core_last_page": 4, "status": "pending"},
    ], "failed_unit": {"index": 0, "code": "MODEL_OUTPUT_TRUNCATED"}}
    from uuid import uuid4
    assert s2_worker._adapt_incomplete_unit(checkpoint, uuid4(), "source-file") is True
    assert checkpoint["units"][0]["core_last_page"] == 1
    assert checkpoint["units"][1]["core_first_page"] == 2


def test_reconcile_repairs_answer_restatements_and_reference_cards(db, tmp_path, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "question_import_dir", tmp_path / "imports")
    monkeypatch.setattr(settings, "question_import_s2_enabled", True)
    monkeypatch.setattr(settings, "question_import_s2_api_key", "test-only")
    monkeypatch.setattr(settings, "question_import_s2_operator_token", "operator-test")
    monkeypatch.setattr(settings, "question_import_s2_base_url", "https://example.invalid/compatible-mode/v1")
    batch = create_batch(db, source_mode="combined", files=[("questions", "combined.pdf", _pdf(3))])
    batch.stage = "awaiting_s2"
    start_s2(db, batch.id, budget_cny=2, operator_token="operator-test")
    source_id = batch.files[0]["file_id"]
    units = plan_units(3, pages_per_unit=1, overlap=0)
    for number, unit in [("1", units[0]), ("1", units[1]), ("7", units[2])]:
        question = parse_model_output({"complete": True, "questions": [_question(number)],
                                       "unresolved_fragments": []}, units[0]).questions[0]
        item = add_candidate(db, batch, candidate_from_question(source_id, question, unit))
        if unit.index == 0:
            payload = dict(item.payload)
            payload["answers"] = [{"answer_id": "old-source", "answer_source": "source_extracted",
                                   "teacher_confirmed": False, "text": "3"}]
            item.revision = 2
            item.payload = payload
    batch.status = "review_pending"
    batch.stage = "s2_completed"
    batch.checkpoint = {"s2": {**batch.checkpoint["s2"],
                               "prompt_version": "question-import-combined-v1",
                               "units": [{"source_answers": [_answer("1", "3")]}]},
                        "s3": {"source_answer_reconciliation": {
                            "schema_version": "s3-source-answer-reconciliation-v1"}}}
    db.flush()

    result = reconcile_source_answers(db, batch.id)
    assert result["removed_nonquestion_candidate_count"] == 0
    assert result["linked_answer_count"] == 0
    assert result["unassigned_answers"][0]["reason"] == "repeated_question_number"
    from app.models import QuestionImportItem
    from sqlalchemy import select
    remaining = list(db.scalars(select(QuestionImportItem).where(QuestionImportItem.batch_id == batch.id)))
    assert len(remaining) == 3
    assert remaining[0].payload["answers"][0]["answer_id"] == "old-source"
    assert "excluded_nonquestion_candidates" not in batch.checkpoint["s3"]
    assert reconcile_source_answers(db, batch.id) == result
