from __future__ import annotations

import json
import shutil
import zipfile
from io import BytesIO
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from pypdf import PdfWriter
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.database import engine
from app.services.question_import import DOCX_CONVERSION_VERSION, ImportProblem, create_batch, retry_batch
from app.services.question_import_s2 import (
    QwenPdfProvider, candidate_from_question, candidate_key, estimate_cost,
    overlap_candidate_is_duplicate, prefer_incoming_overlap, resolved_overlap_option,
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


def _docx_with_math() -> bytes:
    """An inline Word OMML fraction, matching the formula type lost in the sample."""
    document = """<?xml version="1.0" encoding="UTF-8"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"
            xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math">
<w:body><w:p><w:r><w:t>Reciprocal of </w:t></w:r>
<m:oMath><m:r><m:t>-</m:t></m:r><m:f>
<m:num><m:r><m:t>1</m:t></m:r></m:num>
<m:den><m:r><m:t>2</m:t></m:r></m:den>
</m:f></m:oMath><w:r><w:t> is sought.</w:t></w:r></w:p></w:body>
</w:document>"""
    output = BytesIO()
    with zipfile.ZipFile(BytesIO(_docx())) as source, zipfile.ZipFile(output, "w") as target:
        for name in source.namelist():
            target.writestr(name, document if name == "word/document.xml" else source.read(name))
    return output.getvalue()


def test_page_overlap_mapping_and_model_guard():
    units = plan_units(5, pages_per_unit=2, overlap=1)
    assert [(unit.first_page, unit.last_page) for unit in units] == [(1, 3), (3, 5)]
    assert sum(unit.page_count for unit in units) == 6
    assert sum(unit.page_count for unit in plan_units(50, pages_per_unit=2, overlap=1)) == 74
    assert [(unit.first_page, unit.last_page) for unit in plan_units(2, pages_per_unit=2, overlap=1)] == [(1, 2)]
    assert [(unit.first_page, unit.last_page) for unit in plan_units(17, pages_per_unit=2, overlap=1)][-1] == (15, 17)
    assert len(plan_units(17, pages_per_unit=2, overlap=1)) == 8
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
    reused_names = {**question, "subquestions": [{"original_number": "(1)", "question_text": "判断正误"}]}
    reused_output = parse_model_output({"complete": True, "questions": [reused_names],
                                        "unresolved_fragments": []}, units[1])
    reused_candidate = candidate_from_question(str(uuid4()), reused_output.questions[0], units[1])
    assert reused_candidate["extraction"]["subquestions"] == ["(1)判断正误"]
    assert reused_candidate["content"]["question_text"].endswith("(1)判断正误")
    with pytest.raises(ImportProblem, match=r"questions\[0\]\.subquestions\[0\]"):
        parse_model_output({"complete": True, "questions": [{**question, "subquestions": [
            {"original_number": "(1)", "question_text": "判断正误", "extra": "unexpected"}]}],
                            "unresolved_fragments": []}, units[1])
    with pytest.raises(ImportProblem, match="页码"):
        parse_model_output({"complete": True, "questions": [_model_question(page=5)],
                            "unresolved_fragments": []}, units[0])
    with pytest.raises(ImportProblem, match="不完整"):
        parse_model_output({"complete": False, "questions": [],
                            "unresolved_fragments": []}, units[0])
    with pytest.raises(ImportProblem, match="字段"):
        parse_model_output({"complete": True, "questions": [{**question, "is_required": True}],
                            "unresolved_fragments": []}, units[0])


def test_overlap_reconciliation_keeps_distinct_math_and_resolves_attached_option():
    file_id = str(uuid4())
    first_unit, second_unit = plan_units(4, pages_per_unit=2, overlap=1)
    first = candidate_from_question(file_id, parse_model_output({
        "complete": True, "questions": [{
            **_model_question(page=2), "page_end": 3,
            "question_text": "计算 1/2 + 3/4",
            "options": [{"key": "C", "text": "甲错误，乙正确"}],
        }], "unresolved_fragments": [],
    }, first_unit).questions[0], first_unit)
    second = candidate_from_question(file_id, parse_model_output({
        "complete": True, "questions": [{
            **_model_question(page=1), "question_text": "计算 \\frac{1}{2}+\\frac{3}{4}。",
            "options": [{"key": "C", "text": "甲错误，乙正确"}],
        }], "unresolved_fragments": [],
    }, second_unit).questions[0], second_unit)
    assert overlap_candidate_is_duplicate(first, second, boundary_page=3)
    changed_number = json.loads(json.dumps(second))
    changed_number["content"]["question_text"] = "计算 1/2 + 5/4"
    assert not overlap_candidate_is_duplicate(first, changed_number, boundary_page=3)
    changed_option = json.loads(json.dumps(second))
    changed_option["content"]["options"][0]["text"] = "甲乙均正确"
    assert not overlap_candidate_is_duplicate(first, changed_option, boundary_page=3)
    missing_options = json.loads(json.dumps(second))
    missing_options["content"]["options"] = []
    assert not overlap_candidate_is_duplicate(first, missing_options, boundary_page=3)
    assert resolved_overlap_option({"page": 3, "text": "C. 甲错误，乙正确"},
                                   boundary_page=3, previous=[first])
    assert not resolved_overlap_option({"page": 3, "text": "D. 甲、乙均错误"},
                                       boundary_page=3, previous=[first])
    assert not resolved_overlap_option({"page": 4, "text": "C. 甲错误，乙正确"},
                                       boundary_page=3, previous=[first])


def test_overlap_same_page_number_matches_formula_notation_variants():
    source = {"file_id": str(uuid4()), "original_number": "9", "page_start": 5}
    first = {"source": source, "content": {"question_text": "当 -a = -7 时， -a 的相反数是（ ）",
             "options": [{"key": "A", "text": "7"}, {"key": "C", "text": "±7"}]}}
    second = json.loads(json.dumps(first))
    second["content"]["options"][1]["text"] = "$\\pm 7$"
    assert overlap_candidate_is_duplicate(first, second, boundary_page=5)
    different_math = json.loads(json.dumps(second))
    different_math["content"]["question_text"] = "当 -a = -8 时， -a 的相反数是（ ）"
    assert not overlap_candidate_is_duplicate(first, different_math, boundary_page=5)

    first["source"]["original_number"] = "10"
    first["content"]["question_text"] = "化简 -(-(-...(-1)...)))（上方标注“2022 个负号”）的结果的相反数为（ ）"
    first["content"]["options"] = [{"key": "A", "text": "-1"}, {"key": "B", "text": "1"}]
    second = json.loads(json.dumps(first))
    second["content"]["question_text"] = (
        "化简 \\(\\frac{2022 \\text{个负号}}{-(-(-\\cdots(-1)\\cdots)))}\\) 的结果的相反数为（）")
    assert overlap_candidate_is_duplicate(first, second, boundary_page=5)
    second["content"]["options"][1]["text"] = "2"
    assert not overlap_candidate_is_duplicate(first, second, boundary_page=5)


def test_same_unit_partial_question_keeps_complete_candidate():
    from scripts.reconcile_s2_overlap import audit

    source = {"file_id": str(uuid4()), "original_number": "8", "page_start": 13}
    partial = {"source": source, "content": {"question_text": "计算：(1) (-12)-(-15);", "options": []},
               "teacher_decision": "pending"}
    complete = json.loads(json.dumps(partial))
    complete["content"]["question_text"] = "计算：(1) $(-12)-(-15)$; (2) $0-2020$; (3) $(-7.5)-5.6$."
    assert overlap_candidate_is_duplicate(partial, complete, boundary_page=13)
    assert prefer_incoming_overlap(partial, complete)
    items = [SimpleNamespace(id=uuid4(), ordinal=index, revision=1, payload=payload)
             for index, payload in enumerate((partial, complete), start=1)]
    batch = SimpleNamespace(stage="s2_completed", status="review_pending", checkpoint={"s2": {
        "units": [{"first_page": 13, "candidate_count": 2, "unresolved_fragments": []}]}})
    duplicates, unresolved = audit(batch, items)
    assert unresolved == []
    assert duplicates[0]["removed_item_id"] == str(items[0].id)
    assert duplicates[0]["kept_item_id"] == str(items[1].id)


def test_empty_array_response_reports_actual_failure():
    body = {"choices": [{"finish_reason": "stop", "message": {"content": "[]"}}]}
    with pytest.raises(ImportProblem, match="空数组") as error:
        decode_qwen_response(body, page_count=3, elapsed=1.0, model="qwen3.8-flash")
    assert error.value.code == "INVALID_MODEL_OUTPUT"


def test_failed_alias_response_can_be_reused_without_another_model_call(tmp_path, monkeypatch):
    artifact = tmp_path / "saved.json"
    question = {**_model_question(), "subquestions": [
        {"original_number": "(1)", "question_text": "求和"}]}
    body = {"id": "already-paid", "choices": [{"finish_reason": "stop", "message": {
        "content": json.dumps({"complete": True, "questions": [question],
                               "unresolved_fragments": []})}}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 50,
                  "x_tools": {"pdf_page_parser": {"count": 1}}}}
    artifact.write_text(json.dumps({"raw_response": body, "elapsed_seconds": 1.0}), encoding="utf-8")
    monkeypatch.setattr(s2_worker, "_result_path", lambda *_: artifact)
    monkeypatch.setattr(s2_worker, "QwenPdfProvider", lambda: pytest.fail("unexpected paid model call"))
    unit = plan_units(1, pages_per_unit=1, overlap=0)[0]
    output, meta = s2_worker._extract_unit(uuid4(), str(uuid4()), None, unit, retry_invalid=True)
    assert output.questions[0].subquestions[0].original_number == "(1)"
    assert meta["response_id"] == "already-paid"


def test_legacy_redundant_tail_is_removed_only_before_it_starts(tmp_path, monkeypatch):
    monkeypatch.setattr(s2_worker, "_result_path", lambda _batch, _file, index: tmp_path / f"{index}.json")
    units = [
        {"index": 0, "last_page": 3, "core_last_page": 2, "status": "completed"},
        {"index": 1, "last_page": 5, "core_last_page": 4, "status": "completed"},
        {"index": 2, "last_page": 5, "core_last_page": 5, "status": "pending"},
    ]
    checkpoint = {"units": units.copy(), "failed_unit": {"index": 2}}
    assert not s2_worker._trim_redundant_pending_tail(checkpoint, uuid4(), str(uuid4()))
    checkpoint.pop("failed_unit")
    (tmp_path / "2.json").write_text("{}", encoding="utf-8")
    assert not s2_worker._trim_redundant_pending_tail(checkpoint, uuid4(), str(uuid4()))
    (tmp_path / "2.json").unlink()
    assert s2_worker._trim_redundant_pending_tail(checkpoint, uuid4(), str(uuid4()))
    assert [unit["index"] for unit in checkpoint["units"]] == [0, 1]


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
    response_format = captured["response_format"]
    assert response_format["type"] == "json_schema"
    assert response_format["json_schema"]["strict"] is True
    schema = response_format["json_schema"]["schema"]
    assert schema["type"] == "object"
    assert set(schema["required"]) == {"complete", "questions"}
    assert schema["additionalProperties"] is False
    assert "ModelQuestion" in schema["$defs"]
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


@pytest.mark.skipif(shutil.which("libreoffice") is None, reason="container LibreOffice required")
def test_docx_conversion_preserves_inline_word_fraction(db, tmp_path, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "question_import_dir", tmp_path / "imports")
    batch = create_batch(db, source_mode="questions_only", files=[("questions", "math.docx", _docx_with_math())])
    pdf, pages, _ = prepare_pdf(batch.id, batch.files[0])
    assert DOCX_CONVERSION_VERSION in pdf.name
    assert pages == 1
    from pypdf import PdfReader

    text = PdfReader(str(pdf)).pages[0].extract_text()
    assert "Reciprocal of" in text and "is sought." in text
    assert "1" in text and "2" in text


def test_old_docx_s2_pages_cannot_resume_with_new_conversion(db, tmp_path, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "question_import_dir", tmp_path / "imports")
    monkeypatch.setattr(settings, "question_import_s2_enabled", True)
    monkeypatch.setattr(settings, "question_import_s2_api_key", "test-only")
    monkeypatch.setattr(settings, "question_import_s2_operator_token", "operator-test")
    monkeypatch.setattr(settings, "question_import_s2_base_url", "https://example.invalid/compatible-mode/v1")
    batch = create_batch(db, source_mode="questions_only", files=[("questions", "math.docx", _docx_with_math())])
    batch.stage = "awaiting_s2"
    batch.checkpoint = {"s2": {"units": [{"index": 0, "status": "done"}]}}
    db.flush()
    with pytest.raises(ImportProblem) as error:
        start_s2(db, batch.id, budget_cny=2, operator_token="operator-test")
    assert error.value.code == "S2_CONFIG_CHANGED"


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
    monkeypatch.setattr(s2_worker, "prepare_pdf", lambda *_: (tmp_path / "unused.pdf", 4, []))
    monkeypatch.setattr(s2_worker, "pdf_unit_bytes", lambda *_: _pdf())
    calls = []

    class MockProvider:
        def extract(self, data, *, page_count, capture_response=None):
            calls.append(page_count)
            first = len(calls) == 1
            question = {**_model_question(page=2 if first else 1),
                        "page_end": 3 if first else 1,
                        "question_text": "计算 1+2" if first else "计算 1 + 2。",
                        "options": [{"key": "C", "text": "甲错误，乙正确"}]}
            output = parse_model_output({"complete": True, "questions": [question],
                                         "unresolved_fragments": [] if first else [{
                                             "page": 1, "text": "C. 甲错误，乙正确",
                                             "reason": "缺少题干和题号，无法判断归属"}]},
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
    assert calls == [3, 2]
    second_unit = batch.checkpoint["s2"]["units"][1]
    assert second_unit["candidate_count"] == 0
    assert second_unit["unresolved_fragments"] == []
    assert len(second_unit["resolved_overlap_fragments"]) == 1
    assert second_unit["overlap_duplicates"][0]["reason"] == "overlap_variant"
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
            content = "[]" if len(calls) == 1 else json.dumps({"complete": True, "questions": [_model_question()],
                                                                 "unresolved_fragments": []})
            body = {"id": f"response-{len(calls)}", "choices": [{"finish_reason": "stop", "message": {
                "content": content}}],
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

