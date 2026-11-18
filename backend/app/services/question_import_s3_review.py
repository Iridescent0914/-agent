"""Paid, operator-triggered advice for a single unresolved S3 issue."""
from __future__ import annotations

import base64
import copy
import hashlib
import json
import re
import time
import uuid
from datetime import datetime, timezone
from io import BytesIO
from typing import Any, Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from pypdf import PdfReader, PdfWriter
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import QuestionImportBatch, QuestionImportItem
from app.services.question_import import (
    DOCX_CONVERSION_VERSION, OWNER_SCOPE, ImportProblem, active_candidate, require_s2_operator_token,
)
from app.services.question_import_s2 import FLASH_MODEL, estimate_cost, prepare_pdf
from app.services.question_import_s3 import (
    _answer_id, linked_source_answer_item, reconcile_source_answers, source_answer_identity,
)

REVIEW_VERSION = "s3-issue-review-v2"
MAX_REVIEW_PAGES = 4
MAX_REVIEW_CANDIDATES = 8
MAX_GROUP_PAGES = 8
MAX_GROUP_ISSUES = 20
MAX_GROUP_CANDIDATES = 40


class ModelIssueAdvice(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    verdict: Literal["same_question", "different_questions", "match_candidate", "no_match", "false_positive", "uncertain"]
    candidate_item_id: str | None
    evidence_page: int = Field(ge=1)
    evidence_quote: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class ModelGroupAdviceItem(ModelIssueAdvice):
    issue_key: str


class ModelGroupAdvice(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    issues: list[ModelGroupAdviceItem]


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
                             "candidates": [_candidate_context(item) for item in nearby[:MAX_REVIEW_CANDIDATES]]})
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
            elif len(items) <= MAX_REVIEW_CANDIDATES:
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
                         "candidates": [_candidate_context(item) for item in pool[:MAX_REVIEW_CANDIDATES]]})
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
    pages: set[int] = set()
    for first, last in page_ranges:
        if not isinstance(first, int) or not isinstance(last, int) or first < 1 or last < first or last - first >= MAX_REVIEW_PAGES:
            raise ImportProblem("S3_REVIEW_PAGE_INVALID", "问题页码无效或跨度过长，需人工核对", 409)
        pages.update(range(first, last + 1))
    if len(pages) > MAX_REVIEW_PAGES:
        raise ImportProblem("S3_REVIEW_PAGE_LIMIT", "问题原页超过单次复核上限", 409)
    for candidate in issue["candidates"]:
        page = candidate["page_start"]
        if isinstance(page, int) and page > 0 and len(pages) < MAX_REVIEW_PAGES:
            pages.add(page)
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
        try:
            pages = _pages_for_issue(issue)
        except ImportProblem:
            pages = []
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


def _validated_advice(advice: ModelIssueAdvice, issue: dict[str, Any], pages: list[int]) -> dict[str, Any]:
    allowed = {candidate["item_id"] for candidate in issue["candidates"]}
    if advice.evidence_page > len(pages) or (advice.candidate_item_id is not None and advice.candidate_item_id not in allowed):
        raise ImportProblem("INVALID_MODEL_OUTPUT", "模型引用了未发送的页或候选题；本次调用可能已产生费用")
    if issue["kind"] == "overlap":
        valid = advice.verdict in {"same_question", "different_questions", "uncertain"} and advice.candidate_item_id is None
    elif issue["kind"] == "fragment":
        valid = advice.verdict in {"match_candidate", "no_match", "false_positive", "uncertain"} and (
            (advice.verdict == "match_candidate") == (advice.candidate_item_id is not None))
    else:
        valid = advice.verdict in {"match_candidate", "no_match", "uncertain"} and (
            (advice.verdict == "match_candidate") == (advice.candidate_item_id is not None))
    if not valid:
        raise ImportProblem("INVALID_MODEL_OUTPUT", "模型结论与问题类型不符；本次调用可能已产生费用")
    return {**advice.model_dump(mode="json", exclude={"issue_key"}), "source_page": pages[advice.evidence_page - 1]}


class QwenIssueReviewProvider:
    def __init__(self, *, transport: httpx.BaseTransport | None = None):
        self.settings = get_settings()
        self.transport = transport

    def review(self, pdf_data: bytes, issue: dict[str, Any], pages: list[int]) -> tuple[dict[str, Any], dict[str, Any]]:
        prompt = (
            "你是数学资料复核员。只处理以下一个待核对问题。PDF 内页码从 1 开始，"
            f"对应原文件页码 {pages}。候选题 ID 只可从给定列表选择。"
            "结合 PDF 原页与候选题内容给出保守建议；看不清或无法唯一确定时选 uncertain。"
            "evidence_quote 必须是所发送原页中可核对的短原文，evidence_page 是本 PDF 内页码。"
            "不要改写题目、生成答案、删除候选或替教师确认。"
            "跨页重复问题 verdict 只能是 same_question、different_questions、uncertain，candidate_item_id 为 null；"
            "先核对待查片段是否真的出现在原页，不要把首次 OCR 提取结果当作事实。"
            "若原页实际是数轴、图形、文字或其他内容，首次提取的表格或公式并不存在，"
            "片段问题选 false_positive，并说明原页实际内容；不要因此改写候选题。"
            "片段问题 verdict 只能是 match_candidate、no_match、false_positive、uncertain；"
            "no_match 表示片段确实存在但未找到可信归属，不能用于表示 OCR 误读。"
            "答案归属问题 verdict 只能是 match_candidate、no_match、uncertain；"
            "只有 match_candidate 时填写给定候选 ID，其余为 null。"
            + json.dumps({"kind": issue["kind"], "content": issue["content"],
                          "candidates": issue["candidates"]}, ensure_ascii=False)
        )
        payload = {"model": self.settings.question_import_s2_model,
                   "messages": [{"role": "user", "content": [
                       {"type": "file", "file": {"file_data": "data:application/pdf;base64," + base64.b64encode(pdf_data).decode("ascii"),
                                                 "filename": "review-pages.pdf"}},
                       {"type": "text", "text": prompt}]}],
                   "response_format": {"type": "json_schema", "json_schema": {
                       "name": "s3_issue_advice", "strict": True, "schema": ModelIssueAdvice.model_json_schema()}},
                   "include_tool_usage": True, "max_tokens": min(2048, self.settings.question_import_s2_max_output_tokens)}
        start = time.monotonic()
        try:
            with httpx.Client(timeout=self.settings.question_import_s2_timeout_seconds, transport=self.transport) as client:
                response = client.post(self.settings.question_import_s2_base_url.rstrip("/") + "/chat/completions",
                                       headers={"Authorization": "Bearer " + self.settings.question_import_s2_api_key}, json=payload)
                response.raise_for_status()
                body = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise ImportProblem("MODEL_REQUEST_FAILED", "百炼复核请求失败") from exc
        try:
            choice = body["choices"][0]
            if choice["finish_reason"] != "stop":
                raise ValueError("truncated")
            advice = ModelIssueAdvice.model_validate(json.loads(choice["message"]["content"]))
        except (KeyError, IndexError, TypeError, ValueError, ValidationError) as exc:
            raise ImportProblem("INVALID_MODEL_OUTPUT", "模型复核结果无效；本次调用可能已产生费用") from exc
        usage = body.get("usage") or {}
        return _validated_advice(advice, issue, pages), {
            "usage": usage, "estimated_cny": estimate_cost(usage), "elapsed_seconds": round(time.monotonic() - start, 3),
            "response_id": body.get("id")}


class QwenGroupReviewProvider(QwenIssueReviewProvider):
    def review(self, pdf_data: bytes, issues: list[dict[str, Any]], pages: list[int]) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
        candidates = {candidate["item_id"]: candidate for issue in issues for candidate in issue["candidates"]}
        if len(candidates) > MAX_GROUP_CANDIDATES:
            raise ImportProblem("S3_REVIEW_TOO_MANY_CANDIDATES", "本页段候选题过多，请逐条复核", 409)
        prompt = (
            "你是数学资料复核员。请一次复核下面同一页段的所有待核对问题，每个问题分别返回一条建议。"
            f"本 PDF 第 1 至 {len(pages)} 页依次对应原文件页码 {pages}；evidence_page 使用本 PDF 内页码。"
            "每条建议必须保留原问题的 issue_key，不得遗漏、重复或添加问题。"
            "候选 ID 只可从该问题列出的 candidate_item_ids 中选择。"
            "结合 PDF 原页、跨页上下文和候选题文字判断；看不清、候选不足或无法唯一确定时选 uncertain。"
            "evidence_quote 须是所发送原页中可核对的短原文。"
            "跨页重复题 verdict 只能是 same_question、different_questions、uncertain，candidate_item_id 为 null。"
            "先逐项核对待查片段是否真的出现在原页，不要把首次 OCR 提取结果当作事实。"
            "若原页实际是数轴、图形、文字或其他内容，首次提取的片段并不存在，"
            "该片段选 false_positive 并说明原页实际内容。"
            "片段问题 verdict 只能是 match_candidate、no_match、false_positive、uncertain；"
            "no_match 表示片段确实存在但没有可信归属，不能用于表示 OCR 误读。"
            "答案归属问题只能是 match_candidate、no_match、uncertain；"
            "只有 match_candidate 时填写所列候选 ID。不要删题、改题、自动配答案或代替教师确认。"
            + json.dumps({"issues": [{"issue_key": issue["key"], "kind": issue["kind"],
                                      "content": issue["content"],
                                      "candidate_item_ids": [candidate["item_id"] for candidate in issue["candidates"]]}
                                     for issue in issues], "candidates": list(candidates.values())}, ensure_ascii=False)
        )
        payload = {"model": self.settings.question_import_s2_model,
                   "messages": [{"role": "user", "content": [
                       {"type": "file", "file": {"file_data": "data:application/pdf;base64," + base64.b64encode(pdf_data).decode("ascii"),
                                                 "filename": "review-unit-pages.pdf"}},
                       {"type": "text", "text": prompt}]}],
                   "response_format": {"type": "json_schema", "json_schema": {
                       "name": "s3_unit_advice", "strict": True, "schema": ModelGroupAdvice.model_json_schema()}},
                   "include_tool_usage": True, "max_tokens": min(4096, self.settings.question_import_s2_max_output_tokens)}
        start = time.monotonic()
        try:
            with httpx.Client(timeout=self.settings.question_import_s2_timeout_seconds, transport=self.transport) as client:
                response = client.post(self.settings.question_import_s2_base_url.rstrip("/") + "/chat/completions",
                                       headers={"Authorization": "Bearer " + self.settings.question_import_s2_api_key}, json=payload)
                response.raise_for_status()
                body = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise ImportProblem("MODEL_REQUEST_FAILED", "百炼页段复核请求失败") from exc
        try:
            choice = body["choices"][0]
            if choice["finish_reason"] != "stop":
                raise ValueError("truncated")
            output = ModelGroupAdvice.model_validate(json.loads(choice["message"]["content"]))
        except (KeyError, IndexError, TypeError, ValueError, ValidationError) as exc:
            raise ImportProblem("INVALID_MODEL_OUTPUT", "模型页段复核结果无效；本次调用可能已产生费用") from exc
        by_key = {issue["key"]: issue for issue in issues}
        if len(output.issues) != len(issues) or {advice.issue_key for advice in output.issues} != set(by_key):
            raise ImportProblem("INVALID_MODEL_OUTPUT", "模型遗漏、重复或添加了复核问题；本次调用可能已产生费用")
        advice_by_key = {advice.issue_key: _validated_advice(advice, by_key[advice.issue_key], pages)
                         for advice in output.issues}
        usage = body.get("usage") or {}
        return advice_by_key, {"usage": usage, "estimated_cny": estimate_cost(usage),
                               "elapsed_seconds": round(time.monotonic() - start, 3), "response_id": body.get("id")}


def _pdf_pages(pdf_path, pages: list[int]) -> bytes:
    reader = PdfReader(str(pdf_path))
    if any(page > len(reader.pages) for page in pages):
        raise ImportProblem("INVALID_PAGE", "复核页码超出原文件", 409)
    writer = PdfWriter()
    for page in pages:
        writer.add_page(reader.pages[page - 1])
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def _review_call_count(reviews: dict[str, dict[str, Any]]) -> int:
    return len({row.get("call_id") or key for key, row in reviews.items()})


def _review_spent(reviews: dict[str, dict[str, Any]]) -> float:
    return sum(float(row.get("estimated_cny") or row.get("reserved_cny") or 0) for row in reviews.values())


def _batch_review_spent(s3: dict[str, Any]) -> float:
    global_review = s3.get("global_dedupe") or {}
    return (_review_spent(s3.get("issue_reviews") or {})
            + float(global_review.get("estimated_cny") or global_review.get("reserved_cny") or 0))


def _apply_overlap_advice(db: Session, batch: QuestionImportBatch, issue: dict[str, Any],
                          advice: dict[str, Any], *, keep_item_id: str | None = None) -> bool:
    """Remove a model-confirmed duplicate, keeping its full record in the batch audit."""
    if issue["kind"] != "overlap" or advice.get("verdict") != "same_question":
        return False
    checkpoint = dict(batch.checkpoint or {})
    s3 = dict(checkpoint.get("s3") or {})
    removed = dict(s3.get("model_removed_duplicates") or {})
    if issue["key"] in removed:
        return False

    def active_id(item_id: str) -> str:
        for _ in range(len(removed) + 1):
            prior = next((row for row in removed.values() if row["removed_item_id"] == item_id), None)
            if prior is None:
                break
            item_id = prior["kept_item_id"]
        return item_id

    ids = [active_id(issue["content"][field]) for field in ("other_item_id", "candidate_item_id")]
    if ids[0] == ids[1]:
        return False
    items = list(db.scalars(select(QuestionImportItem).where(
        QuestionImportItem.batch_id == batch.id,
        QuestionImportItem.id.in_([uuid.UUID(value) for value in ids]),
    ).with_for_update()))
    if len(items) != 2 or any(not active_candidate(item) or item.revision > 2 or item.payload.get("teacher_decision", "pending") != "pending"
                              or any(answer.get("teacher_confirmed") for answer in item.payload.get("answers") or [])
                              for item in items):
        return False

    def rank(item: QuestionImportItem) -> tuple[int, int, int]:
        payload = item.payload
        content = payload.get("content") or {}
        size = len(content.get("question_text") or "") + sum(len(option.get("text") or "")
                                                              for option in content.get("options") or [])
        # Prefer the earlier source question; answer availability is handled separately.
        return (-int((payload.get("source") or {}).get("page_start") or item.ordinal),
                size, -item.ordinal)

    keeper, duplicate = sorted(items, key=rank, reverse=True)
    if keep_item_id is not None:
        keeper = next((item for item in items if str(item.id) == keep_item_id), keeper)
        duplicate = next(item for item in items if item.id != keeper.id)
    removed[issue["key"]] = {"removed_item_id": str(duplicate.id), "kept_item_id": str(keeper.id),
                             "removed_ordinal": duplicate.ordinal,
                             "removed_payload": copy.deepcopy(duplicate.payload),
                             "source_page": advice["source_page"],
                             "evidence_quote": advice["evidence_quote"], "reason": advice["reason"]}
    s3["model_removed_duplicates"] = removed
    checkpoint["s3"] = s3
    batch.checkpoint = checkpoint
    db.delete(duplicate)
    db.flush()
    return True


def _apply_answer_advice(db: Session, batch: QuestionImportBatch, issue: dict[str, Any],
                         advice: dict[str, Any]) -> bool:
    if issue["kind"] != "answer" or advice.get("verdict") != "match_candidate":
        return False
    answer = issue["content"]
    if (not isinstance(answer.get("source_file_id"), str)
            or not isinstance(answer.get("page_start"), int)
            or not isinstance(answer.get("text"), str)):
        return False
    item = db.scalar(select(QuestionImportItem).where(
        QuestionImportItem.batch_id == batch.id,
        QuestionImportItem.id == uuid.UUID(advice["candidate_item_id"]),
    ).with_for_update())
    if item is None or not active_candidate(item) or item.revision > 2 or item.payload.get("teacher_decision", "pending") != "pending":
        return False
    existing = item.payload.get("answers") or []
    if existing and (len(existing) != 1 or existing[0].get("text") != answer.get("text")):
        return False
    reconciliation = (((batch.checkpoint or {}).get("s3") or {}).get("source_answer_reconciliation") or {})
    pending = reconciliation.get("unassigned_answers") or []
    if answer not in pending:
        return False
    text = (f"{answer['subquestion_number']}：{answer['text']}"
            if answer.get("subquestion_number") else answer["text"])
    version = {"answer_id": _answer_id([answer]), "answer_source": "source_extracted",
               "text": text, "evidence": [{"file_id": answer["source_file_id"],
                                           "page": answer["page_start"], "block_id": None,
                                           "quote": answer["text"][:300]}], "teacher_confirmed": False,
               "source_identity": source_answer_identity(answer)}
    payload = dict(item.payload)
    payload["answers"] = [version]
    payload["answer_match_status"] = "model_matched"
    payload["warnings"] = [warning for warning in payload.get("warnings") or []
                           if warning != "资料答案经原页模型复核关联，尚未由教师确认"]
    payload["warnings"].append("资料答案经原页模型复核关联，尚未由教师确认")
    item.revision += 1
    payload["revision"] = item.revision
    item.payload = payload
    checkpoint = dict(batch.checkpoint or {})
    s3 = dict(checkpoint.get("s3") or {})
    s3["source_answer_reconciliation"] = {
        **reconciliation, "linked_answer_count": int(reconciliation.get("linked_answer_count") or 0) + 1,
        "linked_question_count": int(reconciliation.get("linked_question_count") or 0) + (0 if existing else 1),
        "unassigned_answers": [row for row in pending if row != answer],
        "model_matched_answer_count": int(reconciliation.get("model_matched_answer_count") or 0) + 1,
    }
    assigned = dict(s3.get("model_assigned_answers") or {})
    assigned[issue["key"]] = {"candidate_item_id": str(item.id), "source_page": advice["source_page"],
                              "evidence_quote": advice["evidence_quote"], "reason": advice["reason"]}
    s3["model_assigned_answers"] = assigned
    checkpoint["s3"] = s3
    batch.checkpoint = checkpoint
    return True


def _group_plan(batch: QuestionImportBatch, items: list[QuestionImportItem], unit_index: int,
                reviews: dict[str, dict[str, Any]]) -> dict[str, Any]:
    units = ((batch.checkpoint or {}).get("s2") or {}).get("units") or []
    if unit_index < 0 or unit_index >= len(units):
        raise ImportProblem("NOT_FOUND", "页段不存在", 404)
    pending = [issue for issue in _issue_rows(batch, items)
               if issue["unit_index"] == unit_index and _saved_review(issue, reviews)[0] is None]
    eligible = []
    pages: set[int] = set()
    for issue in pending:
        if issue["kind"] == "answer" and (
            not issue["candidates"] or
            (issue["recall_method"] == "number" and issue["candidate_total"] > MAX_REVIEW_CANDIDATES)
        ):
            continue
        try:
            issue_pages = _pages_for_issue(issue)
        except ImportProblem:
            continue
        eligible.append(issue)
        pages.update(issue_pages)
    candidate_count = len({candidate["item_id"] for issue in eligible for candidate in issue["candidates"]})
    available = bool(eligible) and len(eligible) <= MAX_GROUP_ISSUES and len(pages) <= MAX_GROUP_PAGES and candidate_count <= MAX_GROUP_CANDIDATES
    return {"unit_index": unit_index, "issue_keys": [issue["key"] for issue in eligible],
            "page_numbers": sorted(pages), "candidate_count": candidate_count,
            "skipped_count": len(pending) - len(eligible), "available": available,
            "issues": eligible}


def list_review_units(db: Session, batch_id: uuid.UUID) -> list[dict[str, Any]]:
    batch = db.scalar(select(QuestionImportBatch).where(
        QuestionImportBatch.id == batch_id, QuestionImportBatch.owner_scope == OWNER_SCOPE))
    if batch is None:
        raise ImportProblem("NOT_FOUND", "导入批次不存在", 404)
    items = [item for item in db.scalars(select(QuestionImportItem).where(
        QuestionImportItem.batch_id == batch_id)) if active_candidate(item)]
    units = ((batch.checkpoint or {}).get("s2") or {}).get("units") or []
    reviews = (((batch.checkpoint or {}).get("s3") or {}).get("issue_reviews") or {})
    return [{key: value for key, value in _group_plan(batch, items, index, reviews).items() if key != "issues"}
            for index in range(len(units))]


def review_issue(db: Session, batch_id: uuid.UUID, key: str, *, budget_cny: float,
                 operator_token: str | None = None) -> dict[str, Any]:
    settings = get_settings()
    require_s2_operator_token(operator_token)
    if (not settings.question_import_s2_enabled or not settings.question_import_s2_api_key
            or not settings.question_import_s2_base_url.startswith("https://")
            or settings.question_import_s2_model != FLASH_MODEL):
        raise ImportProblem("S2_NOT_CONFIGURED", "百炼复核尚未配置", 409)
    if not 0 < budget_cny <= settings.question_import_s2_max_budget_cny:
        raise ImportProblem("INVALID_S2_BUDGET", "复核预算超出服务端允许范围")
    batch = db.scalar(select(QuestionImportBatch).where(
        QuestionImportBatch.id == batch_id, QuestionImportBatch.owner_scope == OWNER_SCOPE).with_for_update())
    if batch is None:
        raise ImportProblem("NOT_FOUND", "导入批次不存在", 404)
    if (batch.status != "review_pending" or batch.stage != "s2_completed"
            or batch.files_purged_at is not None or batch.expires_at <= datetime.now(timezone.utc)):
        raise ImportProblem("S3_REVIEW_UNAVAILABLE", "仅可复核原文件仍在的已完成 S2 批次", 409)
    source = next(file for file in batch.files if file["role"] == "questions")
    s2 = (batch.checkpoint or {}).get("s2") or {}
    if source["media_type"] != "application/pdf" and s2.get("conversion_version") != DOCX_CONVERSION_VERSION:
        raise ImportProblem("S3_CONFIG_CHANGED", "DOCX 转换版本已变化，请重新上传", 409)
    items = [item for item in db.scalars(select(QuestionImportItem).where(
        QuestionImportItem.batch_id == batch_id)) if active_candidate(item)]
    issue = next((row for row in _issue_rows(batch, items) if row["key"] == key), None)
    if issue is None:
        saved = (((batch.checkpoint or {}).get("s3") or {}).get("issue_reviews") or {}).get(key)
        if saved is not None:
            return {**saved, "stale": False}
        raise ImportProblem("NOT_FOUND", "待复核问题不存在", 404)
    if issue["kind"] == "answer" and not issue["candidates"]:
        raise ImportProblem("S3_REVIEW_NO_CANDIDATES", "该答案缺少可可靠召回的候选题，请人工核对", 409)
    if issue["kind"] == "answer" and issue["recall_method"] == "number" and issue["candidate_total"] > MAX_REVIEW_CANDIDATES:
        raise ImportProblem("S3_REVIEW_TOO_MANY_CANDIDATES", "同题号候选过多，请人工缩小范围后核对", 409)
    pages = _pages_for_issue(issue)
    if not pages:
        raise ImportProblem("S3_REVIEW_PAGE_INVALID", "待复核问题没有有效页码", 409)
    checkpoint = dict(batch.checkpoint or {})
    s3 = dict(checkpoint.get("s3") or {})
    reviews = dict(s3.get("issue_reviews") or {})
    saved, stale = _saved_review(issue, reviews)
    if saved is not None:
        return {**saved, "stale": stale}
    if _review_call_count(reviews) >= settings.question_import_s2_max_calls:
        raise ImportProblem("CALL_LIMIT_REACHED", "复核调用次数已达上限", 409)
    minimum_cost = (len(pages) * settings.question_import_s2_pdf_page_price_cny
                    + 2048 * settings.question_import_s2_output_million_price_cny / 1_000_000
                    + 10000 * settings.question_import_s2_input_million_price_cny / 1_000_000)
    spent = _batch_review_spent(s3)
    if spent + minimum_cost > budget_cny:
        raise ImportProblem("S3_REVIEW_BUDGET", "复核预算不足以覆盖本次预计费用", 409)
    pdf, _, _ = prepare_pdf(batch.id, source)
    pdf_data = _pdf_pages(pdf, pages)
    fingerprint = hashlib.sha256(json.dumps(issue, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    reviews[key] = {"status": "running", "prompt_version": REVIEW_VERSION,
                    "page_numbers": pages, "started_at": datetime.now(timezone.utc).isoformat(),
                    "issue_fingerprint": fingerprint, "reserved_cny": round(minimum_cost, 6)}
    s3["issue_reviews"] = reviews
    checkpoint["s3"] = s3
    batch.checkpoint = checkpoint
    db.commit()  # Reserve the paid call before network I/O; repeated requests return this record.
    try:
        advice, meta = QwenIssueReviewProvider().review(pdf_data, issue, pages)
        result = {"status": "completed", "prompt_version": REVIEW_VERSION, "page_numbers": pages,
                  "issue_fingerprint": fingerprint, "reserved_cny": round(minimum_cost, 6), "advice": advice, **meta}
    except ImportProblem as exc:
        result = {"status": "failed", "prompt_version": REVIEW_VERSION, "page_numbers": pages,
                  "issue_fingerprint": fingerprint, "error_code": exc.code, "error_message": str(exc),
                  "reserved_cny": round(minimum_cost, 6), "cost_may_have_occurred": True}
    except Exception:
        result = {"status": "failed", "prompt_version": REVIEW_VERSION, "page_numbers": pages,
                  "issue_fingerprint": fingerprint, "error_code": "S3_REVIEW_FAILED",
                  "error_message": "复核服务异常；本次调用可能已产生费用",
                  "reserved_cny": round(minimum_cost, 6), "cost_may_have_occurred": True}
    batch = db.scalar(select(QuestionImportBatch).where(QuestionImportBatch.id == batch_id).with_for_update())
    checkpoint = dict(batch.checkpoint or {})
    s3 = dict(checkpoint.get("s3") or {})
    reviews = dict(s3.get("issue_reviews") or {})
    reviews[key] = result
    s3["issue_reviews"] = reviews
    checkpoint["s3"] = s3
    batch.checkpoint = checkpoint
    applied = result["status"] == "completed" and _apply_overlap_advice(db, batch, issue, result["advice"])
    if applied and batch.source_mode == "combined" and ((batch.checkpoint or {}).get("s3") or {}).get("source_answer_reconciliation"):
        reconcile_source_answers(db, batch_id, force=True)
    assigned = result["status"] == "completed" and _apply_answer_advice(db, batch, issue, result["advice"])
    if applied or assigned:
        result = {**result, "removed_duplicate_count": int(applied),
                  "assigned_answer_count": int(assigned)}
        checkpoint = dict(batch.checkpoint or {})
        s3 = dict(checkpoint.get("s3") or {})
        reviews = dict(s3.get("issue_reviews") or {})
        reviews[key] = result
        s3["issue_reviews"] = reviews
        checkpoint["s3"] = s3
        batch.checkpoint = checkpoint
    db.commit()  # Persist the paid result even if the client disconnects before reading the response.
    return {**result, "stale": False}


def review_unit(db: Session, batch_id: uuid.UUID, unit_index: int, *, budget_cny: float,
                operator_token: str | None = None) -> dict[str, Any]:
    """Review all eligible unresolved issues in one S2 page unit with one model call."""
    settings = get_settings()
    require_s2_operator_token(operator_token)
    if (not settings.question_import_s2_enabled or not settings.question_import_s2_api_key
            or not settings.question_import_s2_base_url.startswith("https://")
            or settings.question_import_s2_model != FLASH_MODEL):
        raise ImportProblem("S2_NOT_CONFIGURED", "百炼复核尚未配置", 409)
    if not 0 < budget_cny <= settings.question_import_s2_max_budget_cny:
        raise ImportProblem("INVALID_S2_BUDGET", "复核预算超出服务端允许范围")
    batch = db.scalar(select(QuestionImportBatch).where(
        QuestionImportBatch.id == batch_id, QuestionImportBatch.owner_scope == OWNER_SCOPE).with_for_update())
    if batch is None:
        raise ImportProblem("NOT_FOUND", "导入批次不存在", 404)
    if (batch.status != "review_pending" or batch.stage != "s2_completed"
            or batch.files_purged_at is not None or batch.expires_at <= datetime.now(timezone.utc)):
        raise ImportProblem("S3_REVIEW_UNAVAILABLE", "仅可复核原文件仍在的已完成 S2 批次", 409)
    source = next(file for file in batch.files if file["role"] == "questions")
    s2 = (batch.checkpoint or {}).get("s2") or {}
    if source["media_type"] != "application/pdf" and s2.get("conversion_version") != DOCX_CONVERSION_VERSION:
        raise ImportProblem("S3_CONFIG_CHANGED", "DOCX 转换版本已变化，请重新上传", 409)
    items = [item for item in db.scalars(select(QuestionImportItem).where(
        QuestionImportItem.batch_id == batch_id)) if active_candidate(item)]
    checkpoint = dict(batch.checkpoint or {})
    s3 = dict(checkpoint.get("s3") or {})
    reviews = dict(s3.get("issue_reviews") or {})
    plan = _group_plan(batch, items, unit_index, reviews)
    issues = plan["issues"]
    pages = plan["page_numbers"]
    if not issues:
        return {"status": "nothing_to_review", "reviewed_count": 0, "skipped_count": plan["skipped_count"],
                "page_numbers": []}
    if not plan["available"]:
        raise ImportProblem("S3_REVIEW_GROUP_LIMIT", "该页段问题、原页或候选题过多，请逐条复核", 409)
    if _review_call_count(reviews) >= settings.question_import_s2_max_calls:
        raise ImportProblem("CALL_LIMIT_REACHED", "复核调用次数已达上限", 409)
    minimum_cost = (len(pages) * settings.question_import_s2_pdf_page_price_cny
                    + 4096 * settings.question_import_s2_output_million_price_cny / 1_000_000
                    + 15000 * settings.question_import_s2_input_million_price_cny / 1_000_000)
    if _batch_review_spent(s3) + minimum_cost > budget_cny:
        raise ImportProblem("S3_REVIEW_BUDGET", "复核预算不足以覆盖本次预计费用", 409)
    pdf, _, _ = prepare_pdf(batch.id, source)
    pdf_data = _pdf_pages(pdf, pages)
    call_id = str(uuid.uuid4())
    fingerprints = {issue["key"]: hashlib.sha256(json.dumps(issue, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
                    for issue in issues}
    for index, issue in enumerate(issues):
        reviews[issue["key"]] = {"status": "running", "prompt_version": REVIEW_VERSION,
                                 "call_id": call_id, "page_numbers": pages,
                                 "started_at": datetime.now(timezone.utc).isoformat(),
                                 "issue_fingerprint": fingerprints[issue["key"]],
                                 "reserved_cny": round(minimum_cost, 6) if index == 0 else 0}
    s3["issue_reviews"] = reviews
    checkpoint["s3"] = s3
    batch.checkpoint = checkpoint
    db.commit()  # Reserve the whole paid call before network I/O.
    try:
        advice_by_key, meta = QwenGroupReviewProvider().review(pdf_data, issues, pages)
        status = "completed"
        error_code = error_message = None
    except ImportProblem as exc:
        advice_by_key = {}
        meta = {}
        status = "failed"
        error_code, error_message = exc.code, str(exc)
    except Exception:
        advice_by_key = {}
        meta = {}
        status = "failed"
        error_code, error_message = "S3_REVIEW_FAILED", "页段复核服务异常；本次调用可能已产生费用"
    batch = db.scalar(select(QuestionImportBatch).where(QuestionImportBatch.id == batch_id).with_for_update())
    checkpoint = dict(batch.checkpoint or {})
    s3 = dict(checkpoint.get("s3") or {})
    reviews = dict(s3.get("issue_reviews") or {})
    for index, issue in enumerate(issues):
        row = {"status": status, "prompt_version": REVIEW_VERSION, "call_id": call_id,
               "page_numbers": pages, "issue_fingerprint": fingerprints[issue["key"]],
               "reserved_cny": round(minimum_cost, 6) if index == 0 else 0}
        if status == "completed":
            row.update({"advice": advice_by_key[issue["key"]],
                        "shared_call_estimated_cny": meta.get("estimated_cny"),
                        "estimated_cny": meta.get("estimated_cny") if index == 0 else 0,
                        "response_id": meta.get("response_id"), "usage": meta.get("usage") if index == 0 else None})
        else:
            row.update({"error_code": error_code, "error_message": error_message,
                        "cost_may_have_occurred": True})
        reviews[issue["key"]] = row
    s3["issue_reviews"] = reviews
    checkpoint["s3"] = s3
    batch.checkpoint = checkpoint
    removed_count = 0
    assigned_count = 0
    if status == "completed":
        for issue in issues:
            removed_count += _apply_overlap_advice(db, batch, issue, advice_by_key[issue["key"]])
        if removed_count and batch.source_mode == "combined" and ((batch.checkpoint or {}).get("s3") or {}).get("source_answer_reconciliation"):
            reconcile_source_answers(db, batch_id, force=True)
        for issue in issues:
            assigned_count += _apply_answer_advice(db, batch, issue, advice_by_key[issue["key"]])
    db.commit()
    return {"status": status, "reviewed_count": len(issues), "skipped_count": plan["skipped_count"],
            "page_numbers": pages, "estimated_cny": meta.get("estimated_cny"),
            "error_message": error_message, "removed_duplicate_count": removed_count,
            "assigned_answer_count": assigned_count}
