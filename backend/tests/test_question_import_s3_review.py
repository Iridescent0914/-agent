from __future__ import annotations

from copy import deepcopy
from io import BytesIO
from types import SimpleNamespace

import pytest
from fastapi.routing import APIRoute
from pypdf import PdfWriter
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import engine
from app.api.routes.question_imports import router
from app.services.question_import import add_candidate, create_batch
from app.services.question_import_s3_review import (
    _issue_rows, _pages_for_issue, list_review_issues,
)


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
    for _ in range(2):
        writer.add_blank_page(width=612, height=792)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def test_issue_list_keeps_all_three_problem_types_without_modifying_batch(db, tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "question_import_dir", tmp_path / "imports")
    batch = create_batch(db, source_mode="combined", files=[("questions", "questions.pdf", _pdf())])
    source_id = batch.files[0]["file_id"]
    first = add_candidate(db, batch, {
        "source": {"file_id": source_id, "page_start": 1, "page_end": 1, "original_number": "1"},
        "content": {"question_text": "计算 1+2", "options": []},
    })
    second = add_candidate(db, batch, {
        "source": {"file_id": source_id, "page_start": 2, "page_end": 2, "original_number": "1"},
        "content": {"question_text": "求 1+2 的值", "options": []},
    })
    batch.checkpoint = {"s2": {"units": [{
        "core_first_page": 1, "core_last_page": 2,
        "overlap_suspects": [{"other_item_id": str(first.id), "candidate_item_id": str(second.id)}],
        "unresolved_fragments": [{"page": 2, "text": "看不清的公式", "reason": "无法确认归属"}],
    }]}, "s3": {"issue_reviews": {"answer:0": {"status": "completed", "advice": {"verdict": "match_candidate"}}},
               "source_answer_reconciliation": {"unassigned_answers": [{
        "source_file_id": source_id, "original_number": "1",
        "page_start": 2, "page_end": 2, "text": "3",
        "reason": "repeated_question_number",
    }]}}}
    db.flush()
    checkpoint_before = deepcopy(batch.checkpoint)
    revisions_before = (first.revision, second.revision)

    issues = list_review_issues(db, batch.id)

    assert {issue["kind"] for issue in issues} == {"answer", "fragment", "overlap"}
    assert next(issue for issue in issues if issue["kind"] == "answer")["answer_text"] == "3"
    assert next(issue for issue in issues if issue["kind"] == "fragment")["fragment_text"] == "看不清的公式"
    assert next(issue for issue in issues if issue["kind"] == "overlap")["candidate_item_ids"] == [
        str(first.id), str(second.id)]
    assert all("review" not in issue for issue in issues)
    assert batch.checkpoint == checkpoint_before
    assert (first.revision, second.revision) == revisions_before


def test_separate_file_unassigned_answers_appear_in_issue_count(db, tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "question_import_dir", tmp_path / "imports")
    batch = create_batch(db, source_mode="separate", files=[
        ("questions", "questions.pdf", _pdf()), ("answers", "answers.pdf", _pdf())])
    answer_file_id = next(file["file_id"] for file in batch.files if file["role"] == "answers")
    answers = [{"source_file_id": answer_file_id, "original_number": str(number),
                "page_start": 1, "page_end": 1, "text": str(number),
                "reason": "question_not_found"} for number in range(1, 9)]
    batch.checkpoint = {"s2": {"units": [{"unresolved_fragments": [
        {"page": 2, "text": "待核对公式", "reason": "识别不确定"}]}]},
        "s3": {"separate_answers": {"units": [{"core_first_page": 1, "core_last_page": 1}]},
               "source_answer_reconciliation": {"unassigned_answers": answers}}}
    db.flush()

    issues = list_review_issues(db, batch.id)

    assert len(issues) == 9
    assert len([issue for issue in issues if issue["kind"] == "answer"]) == 8
    assert len([issue for issue in issues if issue["kind"] == "fragment"]) == 1
    assert all(issue["source_answer"]["source_file_id"] == answer_file_id
               and issue["unit_index"] == 0 for issue in issues if issue["kind"] == "answer")


def test_answer_issue_identity_remains_stable_after_reconciliation():
    answers = [{"source_file_id": "source", "original_number": str(index),
                "page_start": index, "page_end": index, "text": str(index + 1),
                "reason": "question_not_found"} for index in (1, 2)]
    batch = SimpleNamespace(source_mode="combined", checkpoint={
        "s2": {"units": []}, "s3": {"source_answer_reconciliation": {
            "unassigned_answers": answers}}})
    before = _issue_rows(batch, [])
    batch.checkpoint["s3"]["source_answer_reconciliation"]["unassigned_answers"] = answers[1:]
    after = _issue_rows(batch, [])
    assert after[0]["key"] == before[1]["key"]
    assert after[0]["key"] != before[0]["key"]

def test_issue_page_list_is_not_limited_by_old_model_request_budget():
    issue = {"kind": "answer", "content": {"page_start": 1, "page_end": 5},
             "candidates": [{"page_start": 6, "page_end": 7},
                            {"page_start": 8, "page_end": None}]}

    assert _pages_for_issue(issue) == [1, 2, 3, 4, 5, 6, 7, 8]


def test_original_page_review_write_routes_are_removed():
    routes = {(route.path, method) for route in router.routes if isinstance(route, APIRoute)
              for method in route.methods or set()}
    assert any(path.endswith("/s3/review-issues") and method == "GET"
               for path, method in routes)
    assert not any(("/s3/review-issues/" in path or "/s3/review-units" in path)
                   and method == "POST" for path, method in routes)
    assert not any(path.endswith("/s3/review-units") for path, _ in routes)
