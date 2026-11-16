from __future__ import annotations

import json
from io import BytesIO

import httpx
import pytest
from pypdf import PdfWriter
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.database import engine
from app.models import QuestionImportItem
from app.services.question_import import ImportProblem, add_candidate, create_batch
from app.services.question_import_s2 import PageUnit, candidate_from_question, ModelQuestion
from app.services.question_import_s3_separate import (
    QwenSeparateAnswerProvider, decode_answer_response, process_separate_answers_one,
    start_separate_answers, validate_answer_output,
)
import app.services.question_import_s3_separate as separate_service


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


def _pdf() -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def _candidate(batch, db, number: str, text: str):
    question = ModelQuestion(original_number=number, question_text=text,
                             page_start=1, page_end=1)
    return add_candidate(db, batch, candidate_from_question(
        batch.files[0]["file_id"], question, PageUnit(0, 1, 1, 1, 1)))


def _configure(monkeypatch, tmp_path):
    settings = get_settings()
    monkeypatch.setattr(settings, "question_import_dir", tmp_path / "imports")
    monkeypatch.setattr(settings, "question_import_s2_enabled", True)
    monkeypatch.setattr(settings, "question_import_s2_api_key", "test-only")
    monkeypatch.setattr(settings, "question_import_s2_operator_token", "operator-test")
    monkeypatch.setattr(settings, "question_import_s2_base_url", "https://example.invalid/compatible-mode/v1")


def test_answer_only_provider_keeps_source_evidence_and_rejects_bad_pages(monkeypatch):
    captured = {}

    def respond(request: httpx.Request):
        captured.update(json.loads(request.content))
        return httpx.Response(200, json={"id": "answers-1", "choices": [{"finish_reason": "stop",
            "message": {"content": json.dumps({"complete": True, "source_answers": [
                {"original_number": "1", "answer_text": "2", "page_start": 1, "page_end": 1}],
                "unresolved_fragments": []}, ensure_ascii=False)}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 30,
                      "x_tools": {"pdf_page_parser": {"count": 1}}}})

    monkeypatch.setattr(separate_service, "get_settings", lambda: Settings(
        question_import_s2_api_key="test-only",
        question_import_s2_base_url="https://example.invalid/compatible-mode/v1"))
    output, meta = QwenSeparateAnswerProvider(transport=httpx.MockTransport(respond)).extract(
        _pdf(), page_count=1, core_page_count=1)
    assert output.source_answers[0].answer_text == "2"
    assert meta["response_id"] == "answers-1"
    prompt = captured["messages"][0]["content"][1]["text"]
    assert "不要把解题步骤" in prompt
    assert "只输出主体页开始" in prompt
    assert "questions" not in captured["response_format"]["json_schema"]["schema"]["properties"]
    with pytest.raises(ImportProblem) as bad_page:
        validate_answer_output({"complete": True, "source_answers": [
            {"answer_text": "2", "page_start": 2, "page_end": 2}]}, 1)
    assert bad_page.value.code == "INVALID_MODEL_PAGE"
    with pytest.raises(ImportProblem) as extra_field:
        validate_answer_output({"complete": True, "source_answers": [], "questions": []}, 1)
    assert extra_field.value.code == "INVALID_MODEL_OUTPUT"
    with pytest.raises(ImportProblem) as truncated:
        decode_answer_response({"choices": [{"finish_reason": "length", "message": {
            "content": '{"complete":true,"source_answers":[]}'}}]}, page_count=1, elapsed=1)
    assert truncated.value.code == "MODEL_OUTPUT_TRUNCATED"


def test_separate_answer_worker_links_only_unique_number_to_answer_file(db, tmp_path, monkeypatch):
    _configure(monkeypatch, tmp_path)
    batch = create_batch(db, source_mode="separate", files=[
        ("questions", "questions.pdf", _pdf()), ("answers", "answers.pdf", _pdf())])
    first = _candidate(batch, db, "1", "计算一加一")
    _candidate(batch, db, "2", "计算二加二")
    _candidate(batch, db, "2", "计算另一题")
    batch.status = "review_pending"
    batch.stage = "s2_completed"
    batch.checkpoint = {"s2": {"auto_global_dedupe": True},
                        "s3": {"global_dedupe": {"status": "completed"}}}
    with pytest.raises(ImportProblem) as wrong_token:
        start_separate_answers(db, batch.id, budget_cny=2, operator_token="wrong")
    assert wrong_token.value.code == "S2_OPERATOR_REQUIRED"
    start_separate_answers(db, batch.id, budget_cny=2, operator_token="operator-test")
    db.commit()
    connection = db.get_bind()

    def session_factory():
        return Session(bind=connection, expire_on_commit=False,
                       join_transaction_mode="create_savepoint")

    monkeypatch.setattr(separate_service, "SessionLocal", session_factory)

    class MockProvider:
        def extract(self, _pdf_data, *, page_count, core_page_count, capture_response=None):
            assert (page_count, core_page_count) == (1, 1)
            return validate_answer_output({"complete": True, "source_answers": [
                {"original_number": "1", "answer_text": "2", "page_start": 1, "page_end": 1},
                {"original_number": "2", "answer_text": "4", "page_start": 1, "page_end": 1}],
                "unresolved_fragments": []}, 1), {
                "usage": {"prompt_tokens": 100, "completion_tokens": 30,
                          "x_tools": {"pdf_page_parser": {"count": 1}}},
                "response_id": "separate-1", "elapsed_seconds": 1.0}

    monkeypatch.setattr(separate_service, "QwenSeparateAnswerProvider", MockProvider)
    assert process_separate_answers_one() is True
    db.refresh(batch)
    db.refresh(first)
    state = batch.checkpoint["s3"]["separate_answers"]
    result = batch.checkpoint["s3"]["source_answer_reconciliation"]
    assert state["status"] == "completed" and state["calls"] == 1
    assert result["source_answer_count"] == 2
    assert result["linked_question_count"] == 1
    assert result["unassigned_answers"][0]["reason"] == "repeated_question_number"
    answer = first.payload["answers"][0]
    assert answer["teacher_confirmed"] is False
    assert answer["evidence"][0]["file_id"] == batch.files[1]["file_id"]
    assert first.payload["selected_answer_id"] is None
    assert process_separate_answers_one() is False
    assert len(list(db.scalars(select(QuestionImportItem).where(
        QuestionImportItem.batch_id == batch.id)))) == 3


def test_invalid_paid_answer_response_is_recorded_before_explicit_retry(db, tmp_path, monkeypatch):
    _configure(monkeypatch, tmp_path)
    batch = create_batch(db, source_mode="separate", files=[
        ("questions", "questions.pdf", _pdf()), ("answers", "answers.pdf", _pdf())])
    item = _candidate(batch, db, "1", "计算一加一")
    batch.status = "review_pending"
    batch.stage = "s2_completed"
    batch.checkpoint = {"s2": {"auto_global_dedupe": True},
                        "s3": {"global_dedupe": {"status": "completed"}}}
    start_separate_answers(db, batch.id, budget_cny=2, operator_token="operator-test")
    db.commit()
    connection = db.get_bind()

    def session_factory():
        return Session(bind=connection, expire_on_commit=False,
                       join_transaction_mode="create_savepoint")

    monkeypatch.setattr(separate_service, "SessionLocal", session_factory)

    class InvalidProvider:
        def extract(self, _pdf_data, *, page_count, core_page_count, capture_response=None):
            capture_response({"id": "paid-invalid", "choices": [{"finish_reason": "stop",
                "message": {"content": '{"complete":true,"source_answers":[],"questions":[]}'}}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 30,
                          "x_tools": {"pdf_page_parser": {"count": 1}}}}, 1.0)
            raise ImportProblem("INVALID_MODEL_OUTPUT", "答案文件模型返回多余字段")

    monkeypatch.setattr(separate_service, "QwenSeparateAnswerProvider", InvalidProvider)
    assert process_separate_answers_one() is True
    db.refresh(batch)
    db.refresh(item)
    assert batch.checkpoint["s3"]["separate_answers"]["status"] == "failed"
    assert batch.checkpoint["s3"]["separate_answers"]["calls"] == 1
    assert not item.payload["answers"]
    start_separate_answers(db, batch.id, budget_cny=2, operator_token="operator-test")
    db.commit()

    class ValidProvider:
        def extract(self, _pdf_data, *, page_count, core_page_count, capture_response=None):
            return validate_answer_output({"complete": True, "source_answers": [
                {"original_number": "1", "answer_text": "2", "page_start": 1, "page_end": 1}]}, 1), {
                "usage": {"prompt_tokens": 100, "completion_tokens": 30,
                          "x_tools": {"pdf_page_parser": {"count": 1}}},
                "response_id": "paid-valid", "elapsed_seconds": 1.0}

    monkeypatch.setattr(separate_service, "QwenSeparateAnswerProvider", ValidProvider)
    assert process_separate_answers_one() is True
    db.refresh(batch)
    db.refresh(item)
    assert batch.checkpoint["s3"]["separate_answers"]["calls"] == 2
    assert batch.checkpoint["s3"]["separate_answers"]["status"] == "completed"
    assert item.payload["answers"][0]["text"] == "2"
