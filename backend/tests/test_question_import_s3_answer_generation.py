from __future__ import annotations

from io import BytesIO
import json
from types import SimpleNamespace

import httpx
import pytest
from pypdf import PdfWriter
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.database import engine
from app.services import question_import as import_service
from app.services import question_import_s3_answer_generation as generation_service
from app.services.question_import import ImportProblem, add_candidate, create_batch, update_candidate
from app.services.question_import_s3_answer_generation import (
    QwenAnswerGenerationProvider, _groups, process_answer_generation_one,
    start_answer_generation, validate_answers,
)


def _item(number: int):
    return SimpleNamespace(id=f"item-{number}", revision=1, payload={
        "content": {"question_text": f"计算 {number}+1", "options": [], "asset_ids": []},
        "extraction": {"subquestions": []}})


def test_answer_output_requires_all_ids_and_rejects_guesses():
    group = _groups([_item(1), _item(2)])[0]["items"]
    valid = [{"item_id": "item-1", "status": "answered", "answer_text": "2", "uncertainty": None},
             {"item_id": "item-2", "status": "uncertain", "answer_text": None,
              "uncertainty": "题图缺失"}]
    assert len(validate_answers({"answers": valid}, group)) == 2
    for invalid in [valid[:1], [valid[0], valid[0]],
                    [{**valid[0], "answer_text": ""}, valid[1]],
                    [valid[0], {**valid[1], "answer_text": "猜测为3"}],
                    [valid[0], {**valid[1], "uncertainty": None}]]:
        with pytest.raises(ImportProblem) as exc:
            validate_answers({"answers": invalid}, group)
        assert exc.value.code == "S3_ANSWER_OUTPUT_INVALID"


def test_provider_sends_only_selected_questions_and_images(monkeypatch):
    group = _groups([_item(1)])[0]["items"]
    group[0]["options"] = [{"key": "C", "text": "1.2×10^8"}]
    captured = {}

    def respond(request: httpx.Request):
        captured.update(json.loads(request.content))
        return httpx.Response(200, json={"id": "answer-1", "choices": [{"finish_reason": "stop",
            "message": {"content": json.dumps({"answers": [{"item_id": "item-1", "status": "answered",
                "answer_text": "2", "uncertainty": None}]})}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 30}})

    monkeypatch.setattr(generation_service, "get_settings", lambda: Settings(
        question_import_s2_api_key="test-only",
        question_import_s2_base_url="https://example.invalid/v1"))
    results, meta = QwenAnswerGenerationProvider(transport=httpx.MockTransport(respond)).generate(
        group, {"item-1": [("asset-1", b"png-data")]})
    assert results[0]["answer_text"] == "2"
    assert meta["estimated_cny"] is not None
    content = captured["messages"][0]["content"]
    assert "不写解题过程" in content[0]["text"]
    assert "1.2×10^8" in content[0]["text"]
    assert "逐字核对所选选项" in content[0]["text"]
    assert "不能读成 108" in content[0]["text"]
    assert "选择题只写选项编号" in content[0]["text"]
    assert content[1]["text"] == "题目 item-1 的图片 asset-1"
    assert content[2]["image_url"]["url"].startswith("data:image/png;base64,")


@pytest.fixture
def db():
    with engine.connect() as connection:
        transaction = connection.begin()
        session = Session(bind=connection, expire_on_commit=False, join_transaction_mode="create_savepoint")
        try:
            yield session
        finally:
            session.close()
            transaction.rollback()


def _pdf():
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def test_question_only_generation_preserves_manual_answer_and_needs_teacher_review(db, tmp_path, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "question_import_dir", tmp_path / "imports")
    monkeypatch.setattr(settings, "question_import_s2_enabled", True)
    monkeypatch.setattr(settings, "question_import_s2_api_key", "test-only")
    monkeypatch.setattr(settings, "question_import_s2_operator_token", "operator-test")
    monkeypatch.setattr(settings, "question_import_s2_base_url", "https://example.invalid/v1")
    batch = create_batch(db, source_mode="questions_only", files=[("questions", "questions.pdf", _pdf())])
    batch.owner_scope = "s3-answer-generation-test"
    monkeypatch.setattr(generation_service, "OWNER_SCOPE", "s3-answer-generation-test")
    monkeypatch.setattr(import_service, "OWNER_SCOPE", "s3-answer-generation-test")
    first = add_candidate(db, batch, {"content": {"question_text": "计算1+1", "options": [], "asset_ids": []},
                                      "answers": [], "teacher_decision": "pending"})
    second = add_candidate(db, batch, {"content": {"question_text": "计算2+2", "options": [], "asset_ids": []},
                                       "answers": [{"answer_id": "teacher-1", "answer_source": "teacher_entered",
                                                    "text": "4", "evidence": [], "teacher_confirmed": False}],
                                       "selected_answer_id": "teacher-1", "teacher_decision": "pending"})
    third = add_candidate(db, batch, {"content": {"question_text": "计算3+3", "options": [], "asset_ids": []},
                                      "answers": [], "teacher_decision": "pending"})
    batch.status = "review_pending"
    batch.stage = "s2_completed"
    batch.checkpoint = {"s3": {"global_dedupe": {"status": "completed"}}}
    start_answer_generation(db, batch.id, budget_cny=1, operator_token="operator-test",
                            item_ids=[first.id])
    db.commit()
    assert [entry["item_id"] for entry in batch.checkpoint["s3"]["answer_generation"]["groups"][0]["items"]] == [str(first.id)]
    with pytest.raises(ImportProblem):
        update_candidate(db, batch.id, first.id, expected_revision=first.revision,
                         changes={"teacher_answer_text": "2"})

    connection = db.get_bind()
    monkeypatch.setattr(generation_service, "SessionLocal", lambda: Session(
        bind=connection, expire_on_commit=False, join_transaction_mode="create_savepoint"))

    class StubProvider:
        last_response = None

        def generate(self, group, images):
            return ([{"item_id": group[0]["item_id"], "status": "answered", "answer_text": "2",
                     "uncertainty": None}],
                    {"usage": {"prompt_tokens": 100, "completion_tokens": 30},
                     "estimated_cny": 0.01, "elapsed_seconds": 0.1, "response_id": "stub-1"})

    monkeypatch.setattr(generation_service, "QwenAnswerGenerationProvider", StubProvider)
    assert process_answer_generation_one() is True
    db.expire_all()
    db.refresh(first)
    db.refresh(second)
    db.refresh(third)
    assert first.payload["answers"][0]["answer_source"] == "model_generated"
    assert first.payload["answers"][0]["teacher_confirmed"] is False
    assert first.payload["precheck_status"] == "not_run"
    assert first.payload["selected_answer_id"] == first.payload["answers"][0]["answer_id"]
    assert second.payload["answers"][0]["text"] == "4"
    assert third.payload["answers"] == []
    with pytest.raises(ImportProblem) as forged:
        update_candidate(db, batch.id, first.id, expected_revision=first.revision,
                         changes={"answers": [{**first.payload["answers"][0], "teacher_confirmed": True}]})
    assert forged.value.code == "INVALID_CANDIDATE"
    assert process_answer_generation_one() is False
    start_answer_generation(db, batch.id, budget_cny=2, operator_token="operator-test")
    assert [entry["item_id"] for entry in batch.checkpoint["s3"]["answer_generation"]["groups"][-1]["items"]] == [str(third.id)]


def test_generation_rejects_answer_file_modes(db, tmp_path, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "question_import_dir", tmp_path / "imports")
    monkeypatch.setattr(settings, "question_import_s2_enabled", True)
    monkeypatch.setattr(settings, "question_import_s2_api_key", "test-only")
    monkeypatch.setattr(settings, "question_import_s2_operator_token", "operator-test")
    monkeypatch.setattr(settings, "question_import_s2_base_url", "https://example.invalid/v1")
    batch = create_batch(db, source_mode="combined", files=[("questions", "questions.pdf", _pdf())])
    batch.owner_scope = "s3-answer-generation-reject"
    monkeypatch.setattr(generation_service, "OWNER_SCOPE", "s3-answer-generation-reject")
    batch.status = "review_pending"
    batch.stage = "s2_completed"
    batch.checkpoint = {"s3": {"global_dedupe": {"status": "completed"}}}
    with pytest.raises(ImportProblem) as exc:
        start_answer_generation(db, batch.id, budget_cny=1, operator_token="operator-test")
    assert exc.value.code == "S3_ANSWER_MODE"
