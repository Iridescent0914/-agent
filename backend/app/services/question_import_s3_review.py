from __future__ import annotations

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
        if unit.get("skipped_by_teacher"):
            page = unit.get("core_first_page")
            if isinstance(page, int) and page > 0:
                nearby = [item for item in items if isinstance((item.payload.get("source") or {}).get("page_start"), int)
                          and abs(item.payload["source"]["page_start"] - page) <= 2]
                nearby.sort(key=lambda item: (
                    abs(item.payload["source"]["page_start"] - page), item.ordinal, str(item.id)))
                rows.append({"key": f"skipped_page:{unit_index}", "kind": "skipped_page",
                             "unit_index": unit_index, "summary": f"第 {page} 页人工跳过，需核对漏题",
                             "content": {"page": page, "text": "本页自动识别已由教师跳过",
                                         "reason": "请核对原页是否有新开始但尚未提取的题，必要时人工新增"},
                             "candidates": [_candidate_context(item) for item in nearby[:MAX_LIST_CANDIDATES]]})
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
    if batch.source_mode == "separate":
        answer_units = ((checkpoint.get("s3") or {}).get("separate_answers") or {}).get("units") or []
        for unit_index, unit in enumerate(answer_units):
            for index, fragment in enumerate(unit.get("unresolved_fragments") or []):
                page = fragment.get("page")
                if isinstance(page, int) and page > 0:
                    rows.append({"key": f"answer_fragment:{unit_index}:{index}",
                                 "kind": "answer_fragment", "unit_index": unit_index,
                                 "summary": f"第 {page} 页未确定片段", "content": fragment,
                                 "candidates": []})
    reconciliation = (checkpoint.get("s3") or {}).get("source_answer_reconciliation") or {}
    model_state = (checkpoint.get("s3") or {}).get("answer_model_matching") or {}
    model_decisions = {row["answer_id"]: row for group in model_state.get("groups") or []
                       for row in group.get("decisions") or []}
    model_suggestions = {row["answer_id"]: row for row in model_state.get("suggestions") or []}
    model_skipped = {row["answer_id"]: row for row in model_state.get("skipped") or []}
    if batch.source_mode in {"combined", "separate"}:
        answer_units = ((checkpoint.get("s2") or {}) if batch.source_mode == "combined"
                        else ((checkpoint.get("s3") or {}).get("separate_answers") or {})).get("units") or []
        for answer in reconciliation.get("unassigned_answers") or []:
            if linked_source_answer_item(answer, items) is not None:
                continue
            answer_key = "answer:" + source_answer_identity(answer)
            suggestion = model_suggestions.get(source_answer_identity(answer)) or {}
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
            suggested_item = by_id.get(suggestion.get("suggested_item_id"))
            if suggested_item is not None:
                pool = [suggested_item, *(item for item in pool if item.id != suggested_item.id)]
                recall_method = "model_suggestion"
            answer_page = answer.get("page_start")
            answer_unit = next((i for i, unit in enumerate(answer_units)
                                if isinstance(answer_page, int)
                                and unit.get("core_first_page", unit.get("first_page", 0)) <= answer_page
                                <= unit.get("core_last_page", unit.get("last_page", 0))), None)
            rows.append({"key": answer_key, "kind": "answer", "unit_index": answer_unit,
                         "summary": f"第 {answer.get('page_start')} 页待认领答案（{answer.get('reason')}）",
                         "content": answer, "candidate_total": len(pool), "recall_method": recall_method,
                         "model_reason": (suggestion.get("reason")
                                          or (model_decisions.get(source_answer_identity(answer)) or {}).get("reason")
                                          or ("单条答案或候选题文字超过模型输入上限，请人工核对"
                                              if model_skipped.get(source_answer_identity(answer), {}).get("reason")
                                              == "single_record_too_large" else None)),
                         "model_suggestion": suggestion.get("suggested_item_id"),
                         "candidates": [_candidate_context(item) for item in pool[:MAX_LIST_CANDIDATES]]})
        answer_by_id = {source_answer_identity(answer): answer for unit in answer_units
                        for answer in unit.get("source_answers") or []}
        model_warnings = list(model_state.get("review_warnings") or [])
        warned_ids = {row.get("answer_id") for row in model_warnings}
        if model_state.get("status") in {"failed", "pending_budget"}:
            reason = ("模型调用失败，现有关联尚未完成核查，请人工核对或重试"
                      if model_state["status"] == "failed" else
                      "模型配对预算不足，现有关联尚未完成核查，请人工核对或增加预算")
            for group in model_state.get("groups") or []:
                if group.get("status") == "completed":
                    continue
                for pending in (group.get("context") or {}).get("answers") or []:
                    answer_id = pending.get("answer_id")
                    if pending.get("current_item_id") and answer_id not in warned_ids:
                        model_warnings.append({"answer_id": answer_id,
                                               "current_item_id": pending["current_item_id"],
                                               "suggested_item_id": None, "reason": reason,
                                               "evidence_quote": ""})
                        warned_ids.add(answer_id)
        for warning in model_warnings:
            answer = answer_by_id.get(warning.get("answer_id"))
            if not answer:
                continue
            ids = [warning.get("current_item_id"), warning.get("suggested_item_id")]
            disagreement = bool(warning.get("suggested_item_id")
                                and warning.get("suggested_item_id") != warning.get("current_item_id"))
            rows.append({"key": "answer_match_warning:" + warning["answer_id"],
                         "kind": "answer_match_disagreement" if disagreement else "answer_match_warning",
                         "unit_index": None,
                         "summary": (f"第 {answer.get('page_start')} 页模型建议更换关联题"
                                     if disagreement else f"第 {answer.get('page_start')} 页已关联答案待核对"),
                         "content": answer, "model_reason": warning.get("reason"),
                         "model_suggestion": warning.get("suggested_item_id"),
                         "current_item_id": warning.get("current_item_id"),
                         "evidence_quote": warning.get("evidence_quote"),
                         "candidates": [_candidate_context(by_id[item_id]) for item_id in ids
                                        if item_id in by_id]})
    return rows


def _pages_for_issue(issue: dict[str, Any]) -> list[int]:
    content = issue["content"]
    if issue["kind"] == "overlap":
        page_ranges = [(candidate["page_start"], candidate["page_end"]) for candidate in issue["candidates"]]
    elif issue["kind"] in {"answer", "answer_match_warning", "answer_match_disagreement"}:
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
    result = []
    for issue in _issue_rows(batch, items):
        pages = _pages_for_issue(issue)
        result.append({"key": issue["key"], "kind": issue["kind"], "unit_index": issue["unit_index"],
                       "summary": issue["summary"],
                       "fragment_text": issue["content"].get("text") if issue["kind"] in {"fragment", "answer_fragment", "skipped_page"} else None,
                       "fragment_reason": issue["content"].get("reason") if issue["kind"] in {"fragment", "answer_fragment", "skipped_page"} else None,
                       "answer_text": issue["content"].get("text") if issue["kind"] in {"answer", "answer_match_warning", "answer_match_disagreement"} else None,
                       "answer_number": issue["content"].get("original_number") if issue["kind"] in {"answer", "answer_match_warning", "answer_match_disagreement"} else None,
                       "source_answer": issue["content"] if issue["kind"] in {"answer", "answer_match_warning", "answer_match_disagreement"} else None,
                       "model_reason": issue.get("model_reason"),
                       "model_suggestion": issue.get("model_suggestion"),
                       "current_item_id": issue.get("current_item_id"),
                       "evidence_quote": issue.get("evidence_quote"),
                       "page_numbers": pages, "candidate_item_ids": [c["item_id"] for c in issue["candidates"]],
                       "candidate_total": issue.get("candidate_total", len(issue["candidates"])),
                       "recall_method": issue.get("recall_method")})
    return result
