from __future__ import annotations

import hashlib
import json
import re
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import QuestionImportBatch, QuestionImportItem
from app.services.question_import import ImportProblem, OWNER_SCOPE, active_candidate
from app.services.question_import_s3 import linked_source_answer_item, source_answer_identity

MAX_LIST_CANDIDATES = 8


def _candidate_context(item: QuestionImportItem) -> dict[str, Any]:
    payload = item.payload
    source = payload.get("source") or {}
    return {"item_id": str(item.id), "page_start": source.get("page_start"),
            "page_end": source.get("page_end"), "original_number": source.get("original_number"),
            "question_text": (payload.get("content") or {}).get("question_text", "")[:600],
            "options": (payload.get("content") or {}).get("options", [])[:12]}


def _issue_rows(batch: QuestionImportBatch, items: list[QuestionImportItem]) -> list[dict[str, Any]]:
    checkpoint = batch.checkpoint or {}
    by_id = {str(item.id): item for item in items}
    rows: list[dict[str, Any]] = []
    for unit_index, unit in enumerate((checkpoint.get("s2") or {}).get("units") or []):
        for index, pair in enumerate(unit.get("overlap_suspects") or []):
            ids = [pair.get("other_item_id"), pair.get("candidate_item_id")]
            if all(item_id in by_id for item_id in ids):
                rows.append({"key": f"overlap:{unit_index}:{index}", "kind": "overlap", "unit_index": unit_index,
                             "summary": "疑似跨页重复题", "content": pair,
                             "candidates": [_candidate_context(by_id[item_id]) for item_id in ids]})
        for index, fragment in enumerate(unit.get("unresolved_fragments") or []):
            page = fragment.get("page")
            if isinstance(page, int) and page > 0:
                nearby = [item for item in items if isinstance((item.payload.get("source") or {}).get("page_start"), int)
                          and abs(item.payload["source"]["page_start"] - page) <= 2]
                nearby.sort(key=lambda item: (
                    abs(item.payload["source"]["page_start"] - page), item.ordinal, str(item.id)))
                rows.append({"key": f"fragment:{unit_index}:{index}", "kind": "fragment", "unit_index": unit_index,
                             "summary": f"第 {page} 页未确定片段", "content": fragment,
                             "candidates": [_candidate_context(item) for item in nearby[:MAX_LIST_CANDIDATES]]})
    reconciliation = (checkpoint.get("s3") or {}).get("source_answer_reconciliation") or {}
    if batch.source_mode == "combined":
        for answer in reconciliation.get("unassigned_answers") or []:
            if linked_source_answer_item(answer, items) is not None:
                continue
            answer_key = "answer:" + source_answer_identity(answer)
            number = str(answer.get("original_number") or "").strip()
            exact = [item for item in items if str((item.payload.get("source") or {}).get("original_number") or "").strip() == number] if number else []
            excerpt = str(answer.get("question_excerpt") or "").strip()
            if exact:
                pool = exact
                recall_method = "number"
            elif excerpt:
                from difflib import SequenceMatcher
                pool = sorted(items, key=lambda item: SequenceMatcher(
                    None, excerpt, str((item.payload.get("content") or {}).get("question_text") or "")[:300]
                ).ratio(), reverse=True)
                recall_method = "excerpt"
            elif len(items) <= MAX_LIST_CANDIDATES:
                pool = items
                recall_method = "all"
            else:
                pool = []
                recall_method = "none"
            answer_page = answer.get("page_start")
            answer_unit = next((i for i, unit in enumerate((checkpoint.get("s2") or {}).get("units") or [])
                                if isinstance(answer_page, int)
                                and unit.get("core_first_page", unit.get("first_page", 0)) <= answer_page
                                <= unit.get("core_last_page", unit.get("last_page", 0))), None)
            rows.append({"key": answer_key, "kind": "answer", "unit_index": answer_unit,
                         "summary": f"第 {answer.get('page_start')} 页待认领答案（{answer.get('reason')}）",
                         "content": answer, "candidate_total": len(pool), "recall_method": recall_method,
                         "candidates": [_candidate_context(item) for item in pool[:MAX_LIST_CANDIDATES]]})
    return rows


def _issue_fingerprint(issue: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(issue, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def _saved_review(issue: dict[str, Any], reviews: dict[str, dict[str, Any]]) -> tuple[dict[str, Any] | None, bool]:
    saved = reviews.get(issue["key"])
    if saved is not None:
        return saved, saved.get("issue_fingerprint") != _issue_fingerprint(issue)
    # Old answer keys used a shrinking list index. Only reuse a legacy result
    # when its complete original issue fingerprint still matches this answer.
    if issue["kind"] == "answer":
        for key, review in reviews.items():
            if re.fullmatch(r"answer:\d+", key) and review.get("issue_fingerprint") == _issue_fingerprint({
                **issue, "key": key}):
                return review, False
    return None, False


def _pages_for_issue(issue: dict[str, Any]) -> list[int]:
    content = issue["content"]
    if issue["kind"] == "overlap":
        page_ranges = [(candidate["page_start"], candidate["page_end"]) for candidate in issue["candidates"]]
    elif issue["kind"] == "answer":
        page_ranges = [(content.get("page_start"), content.get("page_end"))]
    else:
        page_ranges = [(content.get("page"), content.get("page"))]
    page_ranges.extend((candidate["page_start"], candidate["page_end"]) for candidate in issue["candidates"])
    pages: set[int] = set()
    for first, last in page_ranges:
        if last is None:
            last = first
        if isinstance(first, int) and isinstance(last, int) and 0 < first <= last:
            pages.update(range(first, last + 1))
    return sorted(pages)


def list_review_issues(db: Session, batch_id: uuid.UUID) -> list[dict[str, Any]]:
    batch = db.scalar(select(QuestionImportBatch).where(
        QuestionImportBatch.id == batch_id, QuestionImportBatch.owner_scope == OWNER_SCOPE))
    if batch is None:
        raise ImportProblem("NOT_FOUND", "导入批次不存在", 404)
    items = [item for item in db.scalars(select(QuestionImportItem).where(
        QuestionImportItem.batch_id == batch_id)) if active_candidate(item)]
    reviews = ((batch.checkpoint or {}).get("s3") or {}).get("issue_reviews") or {}
    result = []
    for issue in _issue_rows(batch, items):
        pages = _pages_for_issue(issue)
        review, stale = _saved_review(issue, reviews)
        if review is not None:
            review = {**review, "stale": stale}
        result.append({"key": issue["key"], "kind": issue["kind"], "unit_index": issue["unit_index"],
                       "summary": issue["summary"],
                       "fragment_text": issue["content"].get("text") if issue["kind"] == "fragment" else None,
                       "fragment_reason": issue["content"].get("reason") if issue["kind"] == "fragment" else None,
                       "answer_text": issue["content"].get("text") if issue["kind"] == "answer" else None,
                       "answer_number": issue["content"].get("original_number") if issue["kind"] == "answer" else None,
                       "source_answer": issue["content"] if issue["kind"] == "answer" else None,
                       "page_numbers": pages, "candidate_item_ids": [c["item_id"] for c in issue["candidates"]],
                       "candidate_total": issue.get("candidate_total", len(issue["candidates"])),
                       "recall_method": issue.get("recall_method"),
                       "review": review})
    return result
