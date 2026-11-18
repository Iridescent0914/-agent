from __future__ import annotations

from io import BytesIO

import pytest
from pypdf import PdfWriter
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import engine
from app.models import QuestionImportItem
from app.services.question_import import add_candidate, create_batch
from app.services.question_import_s3_global import dedupe_batch
import app.services.question_import_s3_global as global_service
import app.services.question_import_s3_review as review_service


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
    batch.checkpoint = {"s2": {"prompt_version": "question-import-combined-v5", "units": [
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
    class FakeIssueProvider:
        def review(self, pdf_data, issue, pages):
            assert pages == [1, 8]
            return {"verdict": "match_candidate", "candidate_item_id": str(first.id),
                    "source_page": 8, "evidence_page": 2, "evidence_quote": stem,
                    "reason": "原页的题干与候选题一致"}, {"estimated_cny": 0.01}

    monkeypatch.setattr(review_service, "QwenIssueReviewProvider", FakeIssueProvider)
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
