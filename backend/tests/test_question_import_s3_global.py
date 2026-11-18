from __future__ import annotations

from io import BytesIO
from uuid import uuid4

import pytest
from pypdf import PdfWriter
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import engine
from app.models import QuestionImportItem
from app.services.question_import import add_candidate, create_batch
from app.services.question_import_s3_global import _apply_overlap_advice, dedupe_batch
from app.services.question_import_s3_images import list_images
import app.services.question_import_s3_global as global_service


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


def test_global_model_removes_distant_answer_restatement_then_links_answer(db, tmp_path, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "question_import_dir", tmp_path / "imports")
    monkeypatch.setattr(settings, "question_import_s2_enabled", True)
    monkeypatch.setattr(settings, "question_import_s2_api_key", "test-only")
    monkeypatch.setattr(settings, "question_import_s2_operator_token", "operator-test")
    monkeypatch.setattr(settings, "question_import_s2_base_url", "https://example.invalid/v1")
    writer = PdfWriter()
    for _ in range(8):
        writer.add_blank_page(width=612, height=792)
    pdf = BytesIO()
    writer.write(pdf)
    batch = create_batch(db, source_mode="combined", files=[("questions", "combined.pdf", pdf.getvalue())])
    source_id = batch.files[0]["file_id"]
    stem = "请计算有理数一加一的运算结果，并写出完整计算过程。"
    first = add_candidate(db, batch, {"source": {"file_id": source_id, "page_start": 1,
        "page_end": 1, "original_number": "1"},
        "content": {"question_text": stem, "options": []}, "teacher_decision": "pending", "answers": []})
    repeated = add_candidate(db, batch, {"source": {"file_id": source_id, "page_start": 8,
        "page_end": 8, "original_number": "1"},
        "content": {"question_text": stem, "options": []}, "teacher_decision": "pending", "answers": []})
    batch.status = "review_pending"
    batch.stage = "s2_completed"
    batch.checkpoint = {"s2": {"prompt_version": "question-import-combined-v9", "units": [
        {"index": 0, "source_answers": []},
        {"index": 1, "source_answers": [{"source_file_id": source_id, "original_number": "1",
            "subquestion_number": None, "text": "2", "page_start": 8, "page_end": 8,
            "question_excerpt": "请计算有理数九加九的运算结果，并写出完整计算过程。",
            "uncertain_fragments": []}]}]}}
    db.flush()
    calls = []

    class FakeProvider:
        def review(self, context):
            calls.append(context)
            assert len(context["questions"]) == 2
            return [{"keep_item_id": str(first.id), "remove_item_id": str(repeated.id),
                     "kind": "answer_restatement", "evidence_quote": stem,
                     "reason": "答案区重述了前面的题目"}], {"estimated_cny": 0.01}

    monkeypatch.setattr(global_service, "QwenGlobalDedupProvider", FakeProvider)
    result = dedupe_batch(db, batch.id, budget_cny=1, operator_token="operator-test")
    assert result["status"] == "completed"
    assert result["removed_duplicate_count"] == 1
    assert "auto_answer_assigned_count" not in result
    assert len(calls) == 1
    saved = list(db.scalars(select(QuestionImportItem).where(QuestionImportItem.batch_id == batch.id)))
    assert [item.id for item in saved] == [first.id]
    assert saved[0].payload["answers"] == []
    assert batch.checkpoint["s3"]["source_answer_reconciliation"]["linked_answer_count"] == 0
    assert len(batch.checkpoint["s3"]["source_answer_reconciliation"]["unassigned_answers"]) == 1
    assert dedupe_batch(db, batch.id, budget_cny=1, operator_token="operator-test") == result
    assert len(calls) == 1


@pytest.mark.parametrize("same_hash", [True, False])
def test_global_duplicate_removal_keeps_answer_and_image_assets_separate(db, tmp_path, monkeypatch, same_hash):
    monkeypatch.setattr(get_settings(), "question_import_dir", tmp_path / "imports")
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    pdf = BytesIO()
    writer.write(pdf)
    batch = create_batch(db, source_mode="questions_only", files=[("questions", "questions.pdf", pdf.getvalue())])
    source_id = batch.files[0]["file_id"]
    first = add_candidate(db, batch, {
        "source": {"file_id": source_id, "page_start": 1, "page_end": 1, "original_number": "1"},
        "content": {"question_text": "计算 1+2", "options": []},
    })
    second = add_candidate(db, batch, {
        "source": {"file_id": source_id, "page_start": 1, "page_end": 1, "original_number": "1"},
        "content": {"question_text": "求 1+2 的值", "options": []},
    })
    removed_asset, kept_asset = str(uuid4()), str(uuid4())
    first.payload = {**first.payload,
                     "content": {**first.payload["content"], "asset_ids": [removed_asset]},
                     "answers": [{"text": "3", "answer_source": "source_extracted"}]}
    second.payload = {**second.payload,
                      "content": {**second.payload["content"], "asset_ids": [kept_asset]}}
    batch.checkpoint = {"s3": {"images": {"assets": [
        {"asset_id": removed_asset, "sha256": "same"},
        {"asset_id": kept_asset, "sha256": "same" if same_hash else "different"},
    ]}}}
    db.flush()

    assert _apply_overlap_advice(db, batch,
        {"key": "global:" + str(first.id), "kind": "overlap",
         "content": {"other_item_id": str(second.id), "candidate_item_id": str(first.id)}},
        {"verdict": "same_question", "source_page": 1,
         "evidence_quote": "1+2", "reason": "同题"}, keep_item_id=str(second.id))

    assert second.payload["content"]["asset_ids"] == [kept_asset]
    assert not second.payload.get("answers")
    audit = batch.checkpoint["s3"]["model_removed_duplicates"]["global:" + str(first.id)]
    assert audit["removed_payload"]["content"]["asset_ids"] == [removed_asset]
    assert audit["removed_payload"]["answers"][0]["text"] == "3"
    removed_image = next(asset for asset in list_images(db, batch.id)["assets"]
                         if asset["asset_id"] == removed_asset)
    assert removed_image["duplicate_of_kept_image"] is same_hash
    assert removed_image["removed_candidate_for_item_ids"] == [str(second.id)]
