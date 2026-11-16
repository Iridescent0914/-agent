from __future__ import annotations

import json
from io import BytesIO

import httpx
import pytest
from pypdf import PdfWriter
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import engine
from app.models import QuestionImportItem
from app.services.question_import import ImportProblem, add_candidate, create_batch
from app.services.question_import_s3_review import (
    QwenGroupReviewProvider, QwenIssueReviewProvider, list_review_issues,
    list_review_units, review_issue, review_unit,
)
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


def _pdf(pages=2):
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=612, height=792)
    out = BytesIO()
    writer.write(out)
    return out.getvalue()


def _batch_with_overlap(db, tmp_path, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "question_import_dir", tmp_path / "imports")
    monkeypatch.setattr(settings, "question_import_s2_enabled", True)
    monkeypatch.setattr(settings, "question_import_s2_api_key", "test-only")
    monkeypatch.setattr(settings, "question_import_s2_operator_token", "operator-test")
    monkeypatch.setattr(settings, "question_import_s2_base_url", "https://example.invalid/compatible-mode/v1")
    batch = create_batch(db, source_mode="questions_only", files=[("questions", "questions.pdf", _pdf())])
    batch.status = "review_pending"
    batch.stage = "s2_completed"
    first = add_candidate(db, batch, {"source": {"page_start": 1, "page_end": 1, "original_number": "1"},
                                      "content": {"question_text": "计算 1+2", "options": []}})
    second = add_candidate(db, batch, {"source": {"page_start": 2, "page_end": 2, "original_number": "1"},
                                       "content": {"question_text": "求 1+2 的值", "options": []}})
    batch.checkpoint = {"s2": {"units": [{"overlap_suspects": [{
        "other_item_id": str(first.id), "candidate_item_id": str(second.id),
    }]}]}}
    db.flush()
    return batch, first, second


def test_issue_review_sends_only_issue_pages_and_is_idempotent(db, tmp_path, monkeypatch):
    batch, first, second = _batch_with_overlap(db, tmp_path, monkeypatch)
    calls = []

    class FakeProvider:
        def review(self, pdf_data, issue, pages):
            calls.append((pdf_data, issue, pages))
            return {"verdict": "same_question", "candidate_item_id": None,
                    "evidence_page": 1, "source_page": 1,
                    "evidence_quote": "计算 1+2", "reason": "题干相同"}, {
                        "usage": {"prompt_tokens": 100, "completion_tokens": 30}, "estimated_cny": 0.05}

    monkeypatch.setattr(review_service, "QwenIssueReviewProvider", FakeProvider)
    issues = list_review_issues(db, batch.id)
    assert issues[0]["page_numbers"] == [1, 2]
    first_result = review_issue(db, batch.id, "overlap:0:0", budget_cny=1,
                                operator_token="operator-test")
    second_result = review_issue(db, batch.id, "overlap:0:0", budget_cny=1,
                                 operator_token="operator-test")
    assert first_result == second_result
    assert first_result["status"] == "completed"
    assert len(calls) == 1
    assert calls[0][2] == [1, 2]
    assert len(review_service.PdfReader(BytesIO(calls[0][0])).pages) == 2
    saved = list(db.scalars(select(QuestionImportItem).where(QuestionImportItem.batch_id == batch.id)))
    assert len(saved) == 1
    audit = batch.checkpoint["s3"]["model_removed_duplicates"]["overlap:0:0"]
    assert audit["removed_item_id"] not in {str(item.id) for item in saved}
    assert audit["kept_item_id"] == str(saved[0].id)
    assert audit["removed_payload"]["content"]["question_text"]
    assert not list_review_issues(db, batch.id)


def test_issue_review_requires_operator_token_before_model_call(db, tmp_path, monkeypatch):
    batch, _, _ = _batch_with_overlap(db, tmp_path, monkeypatch)
    with pytest.raises(ImportProblem) as exc:
        review_issue(db, batch.id, "overlap:0:0", budget_cny=1)
    assert exc.value.code == "S2_OPERATOR_REQUIRED"
    assert not ((batch.checkpoint or {}).get("s3") or {}).get("issue_reviews")


def test_separate_question_fragment_reports_content_and_stable_nearby_pages(db, tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "question_import_dir", tmp_path / "imports")
    batch = create_batch(db, source_mode="separate", files=[
        ("questions", "questions.pdf", _pdf(8)), ("answers", "answers.pdf", _pdf(1))])
    batch.status = "review_pending"
    batch.stage = "s2_completed"
    batch.checkpoint = {"s2": {"units": [{"unresolved_fragments": [{
        "page": 7, "text": "数轴标签 1/0", "reason": "无法判断归属",
    }]}]}}
    for page in [6, 6, 8, 7, 7, 7, 7, 7, 7]:
        add_candidate(db, batch, {"source": {"page_start": page, "page_end": page},
                                  "content": {"question_text": f"第 {page} 页题目", "options": []}})
    db.flush()
    items = list(db.scalars(select(QuestionImportItem).where(QuestionImportItem.batch_id == batch.id)))
    forward = review_service._issue_rows(batch, items)[0]
    backward = review_service._issue_rows(batch, list(reversed(items)))[0]
    assert [candidate["item_id"] for candidate in forward["candidates"]] == [
        candidate["item_id"] for candidate in backward["candidates"]]
    issue = list_review_issues(db, batch.id)[0]
    assert issue["kind"] == "fragment"
    assert issue["fragment_text"] == "数轴标签 1/0"
    assert issue["fragment_reason"] == "无法判断归属"
    assert issue["page_numbers"] == [6, 7]
    assert len(issue["candidate_item_ids"]) == 8


def test_issue_review_rejects_budget_below_page_cost(db, tmp_path, monkeypatch):
    batch, _, _ = _batch_with_overlap(db, tmp_path, monkeypatch)
    with pytest.raises(ImportProblem) as exc:
        review_issue(db, batch.id, "overlap:0:0", budget_cny=0.01,
                     operator_token="operator-test")
    assert exc.value.code == "S3_REVIEW_BUDGET"
    assert not ((batch.checkpoint or {}).get("s3") or {}).get("issue_reviews")


def test_unit_review_batches_two_issues_in_one_paid_call(db, tmp_path, monkeypatch):
    batch, _, _ = _batch_with_overlap(db, tmp_path, monkeypatch)
    batch.checkpoint = {"s2": {"units": [{
        **batch.checkpoint["s2"]["units"][0],
        "unresolved_fragments": [{"page": 2, "text": "B. 4", "reason": "选项归属不明"}],
    }]}}
    db.flush()
    calls = []

    class FakeGroupProvider:
        def review(self, pdf_data, issues, pages):
            calls.append((pdf_data, [issue["key"] for issue in issues], pages))
            return {issue["key"]: {"verdict": "uncertain", "candidate_item_id": None,
                                   "evidence_page": 1, "source_page": 1,
                                   "evidence_quote": "题号", "reason": "需教师确认"} for issue in issues}, {
                                       "estimated_cny": 0.07, "usage": {"prompt_tokens": 100}, "response_id": "one-call"}

    monkeypatch.setattr(review_service, "QwenGroupReviewProvider", FakeGroupProvider)
    preview = list_review_units(db, batch.id)[0]
    assert preview["issue_keys"] == ["overlap:0:0", "fragment:0:0"]
    assert preview["page_numbers"] == [1, 2]
    result = review_unit(db, batch.id, 0, budget_cny=1, operator_token="operator-test")
    assert result["status"] == "completed"
    assert result["reviewed_count"] == 2
    assert len(calls) == 1
    reviews = batch.checkpoint["s3"]["issue_reviews"]
    assert reviews["overlap:0:0"]["call_id"] == reviews["fragment:0:0"]["call_id"]
    assert review_service._review_call_count(reviews) == 1
    assert review_service._review_spent(reviews) == pytest.approx(0.07)
    again = review_unit(db, batch.id, 0, budget_cny=1, operator_token="operator-test")
    assert again["status"] == "nothing_to_review"
    assert len(calls) == 1


def test_group_provider_rejects_missing_issue(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "question_import_s2_api_key", "test-only")
    monkeypatch.setattr(settings, "question_import_s2_base_url", "https://example.invalid/compatible-mode/v1")
    issues = [{"key": f"fragment:0:{index}", "kind": "fragment", "content": {"page": 1, "text": "A"},
               "candidates": []} for index in range(2)]

    def respond(_request: httpx.Request):
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {
            "content": json.dumps({"issues": [{"issue_key": "fragment:0:0", "verdict": "uncertain",
                                                "candidate_item_id": None, "evidence_page": 1,
                                                "evidence_quote": "A", "reason": "不确定"}]})}}]})

    provider = QwenGroupReviewProvider(transport=httpx.MockTransport(respond))
    with pytest.raises(ImportProblem) as exc:
        provider.review(_pdf(), issues, [1])
    assert exc.value.code == "INVALID_MODEL_OUTPUT"


def test_group_provider_maps_all_issue_evidence_pages(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "question_import_s2_api_key", "test-only")
    monkeypatch.setattr(settings, "question_import_s2_base_url", "https://example.invalid/compatible-mode/v1")
    issues = [{"key": f"fragment:0:{index}", "kind": "fragment", "content": {"page": index + 1, "text": "选项"},
               "candidates": []} for index in range(2)]

    def respond(request: httpx.Request):
        content = json.loads(request.content)["messages"][0]["content"]
        assert content[0]["file"]["filename"] == "review-unit-pages.pdf"
        assert "fragment:0:0" in content[1]["text"] and "fragment:0:1" in content[1]["text"]
        return httpx.Response(200, json={"id": "group-one-call", "choices": [{"finish_reason": "stop", "message": {
            "content": json.dumps({"issues": [{"issue_key": issue["key"], "verdict": "uncertain",
                                                "candidate_item_id": None, "evidence_page": index + 1,
                                                "evidence_quote": "选项", "reason": "归属不明"}
                                               for index, issue in enumerate(issues)]})}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 60,
                      "x_tools": {"pdf_page_parser": {"count": 2}}}})

    advice, meta = QwenGroupReviewProvider(transport=httpx.MockTransport(respond)).review(_pdf(), issues, [4, 7])
    assert advice["fragment:0:0"]["source_page"] == 4
    assert advice["fragment:0:1"]["source_page"] == 7
    assert meta["estimated_cny"] is not None


def test_unassigned_answer_is_grouped_by_source_page(db, tmp_path, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "question_import_dir", tmp_path / "imports")
    monkeypatch.setattr(settings, "question_import_s2_enabled", True)
    monkeypatch.setattr(settings, "question_import_s2_api_key", "test-only")
    monkeypatch.setattr(settings, "question_import_s2_operator_token", "operator-test")
    monkeypatch.setattr(settings, "question_import_s2_base_url", "https://example.invalid/v1")
    batch = create_batch(db, source_mode="combined", files=[("questions", "combined.pdf", _pdf(2))])
    batch.status = "review_pending"
    batch.stage = "s2_completed"
    add_candidate(db, batch, {"source": {"page_start": 1, "page_end": 1, "original_number": "1"},
                              "content": {"question_text": "求 1+1", "options": []}})
    batch.checkpoint = {"s2": {"units": [{"first_page": 1, "last_page": 2,
                                          "core_first_page": 1, "core_last_page": 2}]},
                        "s3": {"source_answer_reconciliation": {"unassigned_answers": [{
                            "source_file_id": batch.files[0]["file_id"],
                            "original_number": "1", "page_start": 2, "page_end": 2,
                            "text": "2", "reason": "question_excerpt_conflict",
                        }]}}}
    db.flush()
    issues = list_review_issues(db, batch.id)
    assert issues[0]["key"] == "answer:0"
    assert issues[0]["unit_index"] == 0
    assert issues[0]["answer_text"] == "2"
    assert issues[0]["answer_number"] == "1"
    assert list_review_units(db, batch.id)[0]["issue_keys"] == ["answer:0"]

    class FakeProvider:
        def review(self, pdf_data, issue, pages):
            assert pages == [1, 2]
            return {"verdict": "match_candidate", "candidate_item_id": issue["candidates"][0]["item_id"],
                    "evidence_page": 2, "source_page": 2, "evidence_quote": "求 1+1", "reason": "题号与题干一致"}, {
                        "estimated_cny": 0.01}

    monkeypatch.setattr(review_service, "QwenIssueReviewProvider", FakeProvider)
    result = review_issue(db, batch.id, "answer:0", budget_cny=1, operator_token="operator-test")
    assert result["assigned_answer_count"] == 1
    item = db.scalar(select(QuestionImportItem).where(QuestionImportItem.batch_id == batch.id))
    assert item.payload["answers"][0]["text"] == "2"
    assert batch.checkpoint["s3"]["source_answer_reconciliation"]["unassigned_answers"] == []
    assert review_issue(db, batch.id, "answer:0", budget_cny=1, operator_token="operator-test") == result


def test_model_review_rejects_unlisted_candidate_and_page(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "question_import_s2_api_key", "test-only")
    monkeypatch.setattr(settings, "question_import_s2_base_url", "https://example.invalid/compatible-mode/v1")
    issue = {"kind": "answer", "content": {"text": "2", "page_start": 2},
             "candidates": [{"item_id": "listed", "page_start": 1, "page_end": 1,
                             "original_number": "1", "question_text": "1+1", "options": []}]}

    def respond(request: httpx.Request):
        body = json.loads(request.content)
        assert body["messages"][0]["content"][0]["file"]["file_data"].startswith("data:application/pdf;base64,")
        return httpx.Response(200, json={"id": "review-1", "choices": [{"finish_reason": "stop", "message": {
            "content": json.dumps({"verdict": "match_candidate", "candidate_item_id": "invented",
                                   "evidence_page": 3, "evidence_quote": "2", "reason": "猜测"})}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 20,
                      "x_tools": {"pdf_page_parser": {"count": 2}}}})

    provider = QwenIssueReviewProvider(transport=httpx.MockTransport(respond))
    with pytest.raises(ImportProblem) as exc:
        provider.review(_pdf(), issue, [1, 2])
    assert exc.value.code == "INVALID_MODEL_OUTPUT"


def test_model_review_maps_local_evidence_page_to_original(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "question_import_s2_api_key", "test-only")
    monkeypatch.setattr(settings, "question_import_s2_base_url", "https://example.invalid/compatible-mode/v1")
    issue = {"kind": "overlap", "content": {"reason": "overlap_variant_requires_review"},
             "candidates": [{"item_id": "a", "page_start": 5, "page_end": 5,
                             "original_number": "1", "question_text": "1+1", "options": []},
                            {"item_id": "b", "page_start": 8, "page_end": 8,
                             "original_number": "1", "question_text": "1+1", "options": []}]}

    def respond(_request: httpx.Request):
        return httpx.Response(200, json={"id": "review-2", "choices": [{"finish_reason": "stop", "message": {
            "content": json.dumps({"verdict": "same_question", "candidate_item_id": None,
                                   "evidence_page": 2, "evidence_quote": "1+1", "reason": "同题"})}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 20,
                      "x_tools": {"pdf_page_parser": {"count": 2}}}})

    advice, meta = QwenIssueReviewProvider(transport=httpx.MockTransport(respond)).review(_pdf(), issue, [5, 8])
    assert advice["source_page"] == 8
    assert advice["verdict"] == "same_question"
    assert meta["estimated_cny"] is not None


def test_fragment_review_can_identify_ocr_false_positive(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "question_import_s2_api_key", "test-only")
    monkeypatch.setattr(settings, "question_import_s2_base_url", "https://example.invalid/compatible-mode/v1")
    issue = {"kind": "fragment", "content": {"page": 7, "text": "1/0", "reason": "suspected table"},
             "candidates": []}

    def respond(request: httpx.Request):
        prompt = json.loads(request.content)["messages"][0]["content"][1]["text"]
        assert "先核对待查片段是否真的出现在原页" in prompt
        assert "false_positive" in prompt
        return httpx.Response(200, json={"id": "review-false-positive", "choices": [{
            "finish_reason": "stop", "message": {"content": json.dumps({
                "verdict": "false_positive", "candidate_item_id": None,
                "evidence_page": 1, "evidence_quote": "a, b, 0, c",
                "reason": "The original page contains a number line, not a table.",
            })}}]})

    advice, _ = QwenIssueReviewProvider(transport=httpx.MockTransport(respond)).review(_pdf(), issue, [7])
    assert advice["verdict"] == "false_positive"
    assert advice["source_page"] == 7


def test_fragment_false_positive_is_saved_on_first_review_without_changing_questions(db, tmp_path, monkeypatch):
    batch, first, second = _batch_with_overlap(db, tmp_path, monkeypatch)
    batch.checkpoint = {"s2": {"units": [{"unresolved_fragments": [{
        "page": 2, "text": "1/0", "reason": "suspected OCR table",
    }]}]}}
    db.flush()
    calls = []

    class FakeProvider:
        def review(self, pdf_data, issue, pages):
            calls.append((issue["kind"], pages))
            return {"verdict": "false_positive", "candidate_item_id": None,
                    "evidence_page": 2, "source_page": 2,
                    "evidence_quote": "a, b, 0, c", "reason": "The page has a number line."}, {
                        "estimated_cny": 0.02, "response_id": "new-review"}

    monkeypatch.setattr(review_service, "QwenIssueReviewProvider", FakeProvider)
    result = review_issue(db, batch.id, "fragment:0:0", budget_cny=1,
                          operator_token="operator-test")
    assert result["advice"]["verdict"] == "false_positive"
    assert calls == [("fragment", [1, 2])]
    assert review_service._review_call_count(batch.checkpoint["s3"]["issue_reviews"]) == 1
    assert review_service._review_spent(batch.checkpoint["s3"]["issue_reviews"]) == pytest.approx(0.02)
    assert {item.id for item in db.scalars(select(QuestionImportItem).where(
        QuestionImportItem.batch_id == batch.id))} == {first.id, second.id}
    assert review_issue(db, batch.id, "fragment:0:0", budget_cny=1,
                        operator_token="operator-test") == result
    assert len(calls) == 1
