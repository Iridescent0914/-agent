from __future__ import annotations

import json
import shutil
import zipfile
from io import BytesIO
from uuid import uuid4

import httpx
import pytest
from pypdf import PdfWriter
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.database import engine
from app.services.question_import import ImportProblem, create_batch, retry_batch
from app.services.question_import_s2 import (
    QwenPdfProvider, candidate_from_question, candidate_key, estimate_cost,
    decode_qwen_response, parse_model_output, plan_units, prepare_pdf,
)
from app.services.question_import_s2_worker import claim_s2, start_s2
import app.services.question_import_s2_worker as s2_worker


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


def _model_question(page: int = 1) -> dict:
    return {
        "original_number": "3", "question_text": "计算 1+2", "options": [],
        "subquestions": ["（1）求和"], "math_fragments": ["1+2"],
        "diagram_clues": [], "page_start": page, "page_end": page,
        "uncertain_fragments": [],
    }


def _docx() -> bytes:
    output = BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("[Content_Types].xml", """<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>""")
        archive.writestr("_rels/.rels", """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>""")
        archive.writestr("word/document.xml", """<?xml version="1.0" encoding="UTF-8"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
<w:body><w:p><w:r><w:t>Test question 1 + 2 = ?</w:t></w:r></w:p></w:body>
</w:document>""")
    return output.getvalue()


def test_page_overlap_mapping_and_model_guard():
    units = plan_units(5, pages_per_unit=2, overlap=1)
    assert [(unit.first_page, unit.last_page) for unit in units] == [(1, 3), (3, 5), (5, 5)]
    assert sum(unit.page_count for unit in units) == 7
    assert sum(unit.page_count for unit in plan_units(50, pages_per_unit=2, overlap=1)) == 74
    assert [(unit.first_page, unit.last_page) for unit in plan_units(2, pages_per_unit=2, overlap=1)] == [(1, 2)]
    question = _model_question(page=2)
    output = parse_model_output({"complete": True, "questions": [question],
                                 "unresolved_fragments": []}, units[1])
    candidate = candidate_from_question(str(uuid4()), output.questions[0], units[1])
    assert candidate["source"]["page_start"] == 4
    assert candidate["content"]["question_text"].endswith("（1）求和")
    assert candidate["tags"]["is_required"] is False
    assert candidate["teacher_decision"] == "pending"
    assert candidate_key(candidate) == candidate_key(candidate)
    structured = {**question, "subquestions": [{"key": "①", "text": "判断正误"}]}
    structured_output = parse_model_output({"complete": True, "questions": [structured],
                                            "unresolved_fragments": []}, units[1])
    structured_candidate = candidate_from_question(str(uuid4()), structured_output.questions[0], units[1])
    assert structured_candidate["content"]["question_text"].endswith("①判断正误")
    assert structured_candidate["extraction"]["subquestions"] == ["①判断正误"]
    numbered = {**question, "subquestions": [{"number": "(1)", "text": "判断正误"}]}
    numbered_output = parse_model_output({"complete": True, "questions": [numbered],
                                          "unresolved_fragments": []}, units[1])
    numbered_candidate = candidate_from_question(str(uuid4()), numbered_output.questions[0], units[1])
    assert numbered_candidate["extraction"]["subquestions"] == ["(1)判断正误"]
    labeled = {**question, "subquestions": [{"label": "①", "text": "判断正误"}]}
    labeled_output = parse_model_output({"complete": True, "questions": [labeled],
                                         "unresolved_fragments": []}, units[1])
    labeled_candidate = candidate_from_question(str(uuid4()), labeled_output.questions[0], units[1])
    assert labeled_candidate["extraction"]["subquestions"] == ["①判断正误"]
    with pytest.raises(ImportProblem, match="页码"):
        parse_model_output({"complete": True, "questions": [_model_question(page=5)],
                            "unresolved_fragments": []}, units[0])
    with pytest.raises(ImportProblem, match="不完整"):
        parse_model_output({"complete": False, "questions": [],
                            "unresolved_fragments": []}, units[0])
    with pytest.raises(ImportProblem, match="字段"):
        parse_model_output({"complete": True, "questions": [{**question, "is_required": True}],
                            "unresolved_fragments": []}, units[0])


def test_qwen_private_file_payload_and_cost():
    captured = {}
    saved = []

    def respond(request: httpx.Request):
        captured.update(json.loads(request.content))
        assert request.url.path.endswith("/chat/completions")
        assert request.headers["authorization"] == "Bearer test-only"
        return httpx.Response(200, json={
            "id": "response-1", "choices": [{"finish_reason": "stop", "message": {
                "content": json.dumps({"complete": True, "questions": [{**_model_question(),
                                       "region": [0.1, 0.2, 0.8, 0.9]}],
                                       "unresolved_fragments": []})}}],
            "usage": {"prompt_tokens": 10000, "completion_tokens": 3000,
                      "x_tools": {"pdf_page_parser": {"count": 1}}},
        })

    settings = Settings(question_import_s2_api_key="test-only",
                        question_import_s2_base_url="https://example.invalid/compatible-mode/v1")
    provider = QwenPdfProvider(settings=settings, transport=httpx.MockTransport(respond))
    output, meta, _ = provider.extract(_pdf(), page_count=1,
                                       capture_response=lambda body, elapsed: saved.append(body))
    assert output.questions[0].question_text == "计算 1+2"
    assert "region" not in json.loads(saved[0]["choices"][0]["message"]["content"])["questions"][0]
    assert "region" not in output.questions[0].model_dump()
    assert captured["messages"][0]["content"][0]["file"]["file_data"].startswith("data:application/pdf;base64,")
    assert "file_url" not in captured["messages"][0]["content"][0]["file"]
    assert captured["include_tool_usage"] is True
    assert estimate_cost(meta["usage"], settings=settings) == 0.0361
    assert estimate_cost({"prompt_tokens": 10, "completion_tokens": 2}, settings=settings) is None


def test_only_flash_model_can_be_called():
    settings = Settings(question_import_s2_model="unapproved-model",
                        question_import_s2_api_key="test-only",
                        question_import_s2_base_url="https://example.invalid/compatible-mode/v1")
    with pytest.raises(ImportProblem) as error:
        QwenPdfProvider(settings=settings)
    assert error.value.code == "S2_MODEL_NOT_SUPPORTED"


def test_invalid_paid_response_is_captured_before_validation():
    captured = []

    def respond(_request: httpx.Request):
        return httpx.Response(200, json={
            "id": "invalid-response", "choices": [{"finish_reason": "stop", "message": {
                "content": json.dumps({"complete": True, "questions": [{**_model_question(), "is_required": True}],
                                       "unresolved_fragments": []})}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 50,
                      "x_tools": {"pdf_page_parser": {"count": 1}}},
        })

    settings = Settings(question_import_s2_api_key="test-only",
                        question_import_s2_base_url="https://example.invalid/compatible-mode/v1")
    provider = QwenPdfProvider(settings=settings, transport=httpx.MockTransport(respond))
    with pytest.raises(ImportProblem) as error:
        provider.extract(_pdf(), page_count=1,
                         capture_response=lambda body, elapsed: captured.append((body, elapsed)))
    assert error.value.code == "INVALID_MODEL_OUTPUT"
    assert captured[0][0]["id"] == "invalid-response"
    assert estimate_cost(captured[0][0]["usage"], settings=settings) == 0.020215


def test_explicit_start_and_s2_retry(db, tmp_path, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "question_import_dir", tmp_path / "imports")
    batch = create_batch(db, source_mode="questions_only", files=[("questions", "one.pdf", _pdf())])
    batch.stage = "awaiting_s2"
    db.flush()
    monkeypatch.setattr(settings, "question_import_s2_enabled", False)
    with pytest.raises(ImportProblem) as disabled:
        start_s2(db, batch.id, budget_cny=2)
    assert disabled.value.code == "S2_NOT_CONFIGURED"
    monkeypatch.setattr(settings, "question_import_s2_enabled", True)
    monkeypatch.setattr(settings, "question_import_s2_api_key", "test-only")
    monkeypatch.setattr(settings, "question_import_s2_operator_token", "operator-test")
    monkeypatch.setattr(settings, "question_import_s2_base_url", "https://example.invalid/compatible-mode/v1")
    with pytest.raises(ImportProblem) as missing_token:
        start_s2(db, batch.id, budget_cny=2)
    assert missing_token.value.code == "S2_OPERATOR_REQUIRED"
    start_s2(db, batch.id, budget_cny=2, operator_token="operator-test")
    assert batch.stage == "s2_queued"
    assert batch.checkpoint["s2"]["budget_cny"] == 2
    claimed = claim_s2(db)
    assert claimed is not None and claimed[0] == batch.id
    assert claim_s2(db) is None
    batch.status = "failed"
    batch.stage = "s2_failed"
    with pytest.raises(ImportProblem) as retry_without_token:
        retry_batch(db, batch.id)
    assert retry_without_token.value.code == "S2_OPERATOR_REQUIRED"
    retry_batch(db, batch.id, operator_token="operator-test")
    assert batch.stage == "s2_queued"
    assert batch.checkpoint["s2"]["budget_cny"] == 2


def test_blank_pdf_page_is_review_warning(db, tmp_path, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "question_import_dir", tmp_path / "imports")
    batch = create_batch(db, source_mode="questions_only", files=[("questions", "blank.pdf", _pdf())])
    pdf, pages, warnings = prepare_pdf(batch.id, batch.files[0])
    assert pdf.is_file()
    assert pages == 1
    assert any("空白" in warning for warning in warnings)


@pytest.mark.skipif(shutil.which("libreoffice") is None, reason="container LibreOffice required")
def test_docx_conversion_preserves_readable_pdf(db, tmp_path, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "question_import_dir", tmp_path / "imports")
    batch = create_batch(db, source_mode="questions_only", files=[("questions", "sample.docx", _docx())])
    pdf, pages, warnings = prepare_pdf(batch.id, batch.files[0])
    assert pdf.is_file()
    assert pages == 1
    assert warnings
    from pypdf import PdfReader
    assert "Test question" in PdfReader(str(pdf)).pages[0].extract_text()


def test_worker_resumes_by_unit_and_deduplicates_overlap(db, tmp_path, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "question_import_dir", tmp_path / "imports")
    monkeypatch.setattr(settings, "question_import_s2_enabled", True)
    monkeypatch.setattr(settings, "question_import_s2_api_key", "test-only")
    monkeypatch.setattr(settings, "question_import_s2_operator_token", "operator-test")
    monkeypatch.setattr(settings, "question_import_s2_base_url", "https://example.invalid/compatible-mode/v1")
    monkeypatch.setattr(settings, "question_import_s2_pages_per_unit", 2)
    monkeypatch.setattr(settings, "question_import_s2_overlap_pages", 1)
    batch = create_batch(db, source_mode="questions_only", files=[("questions", "one.pdf", _pdf())])
    batch.stage = "awaiting_s2"
    db.flush()
    start_s2(db, batch.id, budget_cny=2, operator_token="operator-test")
    db.commit()
    connection = db.get_bind()

    def session_factory():
        return Session(bind=connection, expire_on_commit=False,
                       join_transaction_mode="create_savepoint")

    monkeypatch.setattr(s2_worker, "SessionLocal", session_factory)
    monkeypatch.setattr(s2_worker, "prepare_pdf", lambda *_: (tmp_path / "unused.pdf", 3, []))
    monkeypatch.setattr(s2_worker, "pdf_unit_bytes", lambda *_: _pdf())
    calls = []

    class MockProvider:
        def extract(self, data, *, page_count, capture_response=None):
            calls.append(page_count)
            question = _model_question(page=3 if len(calls) == 1 else 1)
            output = parse_model_output({"complete": True, "questions": [question],
                                         "unresolved_fragments": []},
                                        plan_units(page_count, pages_per_unit=page_count, overlap=0)[0])
            return output, {"usage": {"prompt_tokens": 100, "completion_tokens": 50,
                                      "x_tools": {"pdf_page_parser": {"count": page_count}}},
                            "elapsed_seconds": 1.0, "response_id": f"response-{len(calls)}"}, 1.0

    monkeypatch.setattr(s2_worker, "QwenPdfProvider", MockProvider)
    assert s2_worker.process_s2_one() is True
    db.refresh(batch)
    assert batch.stage == "s2_queued"
    assert batch.checkpoint["s2"]["calls"] == 1
    assert s2_worker.process_s2_one() is True
    db.refresh(batch)
    assert batch.stage == "s2_completed"
    assert batch.checkpoint["s2"]["calls"] == 2
    assert calls == [3, 1]
    from app.models import QuestionImportItem
    from sqlalchemy import select
    assert len(list(db.scalars(select(QuestionImportItem).where(QuestionImportItem.batch_id == batch.id)))) == 1


def test_failed_response_cost_is_counted_and_retry_calls_again(db, tmp_path, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "question_import_dir", tmp_path / "imports")
    monkeypatch.setattr(settings, "question_import_s2_enabled", True)
    monkeypatch.setattr(settings, "question_import_s2_api_key", "test-only")
    monkeypatch.setattr(settings, "question_import_s2_operator_token", "operator-test")
    monkeypatch.setattr(settings, "question_import_s2_base_url", "https://example.invalid/compatible-mode/v1")
    batch = create_batch(db, source_mode="questions_only", files=[("questions", "one.pdf", _pdf())])
    batch.stage = "awaiting_s2"
    db.flush()
    start_s2(db, batch.id, budget_cny=1, operator_token="operator-test")
    db.commit()
    connection = db.get_bind()

    def session_factory():
        return Session(bind=connection, expire_on_commit=False,
                       join_transaction_mode="create_savepoint")

    monkeypatch.setattr(s2_worker, "SessionLocal", session_factory)
    monkeypatch.setattr(s2_worker, "prepare_pdf", lambda *_: (tmp_path / "unused.pdf", 1, []))
    monkeypatch.setattr(s2_worker, "pdf_unit_bytes", lambda *_: _pdf())
    calls = []

    class ResponseProvider:
        def extract(self, _data, *, page_count, capture_response):
            calls.append(len(calls) + 1)
            question = _model_question()
            if len(calls) == 1:
                question["is_required"] = True
            body = {"id": f"response-{len(calls)}", "choices": [{"finish_reason": "stop", "message": {
                "content": json.dumps({"complete": True, "questions": [question], "unresolved_fragments": []})}}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 50,
                          "x_tools": {"pdf_page_parser": {"count": 1}}}}
            capture_response(body, 1.0)
            output, meta = decode_qwen_response(body, page_count=page_count, elapsed=1.0,
                                                model=settings.question_import_s2_model)
            return output, meta, 1.0

    monkeypatch.setattr(s2_worker, "QwenPdfProvider", ResponseProvider)
    assert s2_worker.process_s2_one() is True
    db.refresh(batch)
    assert batch.stage == "s2_failed"
    assert batch.checkpoint["s2"]["estimated_cny"] == 0.020215
    assert batch.checkpoint["s2"]["calls"] == 1
    retry_batch(db, batch.id, operator_token="operator-test")
    db.commit()
    assert s2_worker.process_s2_one() is True
    db.refresh(batch)
    assert batch.stage == "s2_completed"
    assert batch.checkpoint["s2"]["estimated_cny"] == 0.04043
    assert batch.checkpoint["s2"]["calls"] == 2
    assert calls == [1, 2]

