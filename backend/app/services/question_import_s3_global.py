"""Whole-batch model deduplication before source-answer reconciliation."""
from __future__ import annotations

import copy
import json
import logging
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.models import QuestionImportBatch, QuestionImportItem
from app.services.question_import import OWNER_SCOPE, ImportProblem, active_candidate, require_s2_operator_token
from app.services.question_import_s2 import FLASH_MODEL, estimate_cost
from app.services.question_import_s3 import reconcile_source_answers

GLOBAL_DEDUPE_VERSION = "s3-global-dedupe-v2"
GLOBAL_DEDUPE_MAX_OUTPUT_TOKENS = 8192
logger = logging.getLogger(__name__)


def _batch_review_spent(s3: dict[str, Any]) -> float:
    """Return the amount spent on whole-batch deduplication."""
    global_review = s3.get("global_dedupe") or {}
    return float(
        global_review.get("estimated_cny") or global_review.get("reserved_cny") or 0)


def _apply_overlap_advice(db: Session, batch: QuestionImportBatch, issue: dict[str, Any],
                          advice: dict[str, Any], *, keep_item_id: str | None = None) -> bool:
    """Remove a confirmed duplicate and retain its full record in the batch audit."""
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


class DuplicateDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    keep_item_id: str
    remove_item_id: str
    kind: Literal["answer_restatement", "same_question"]
    evidence_quote: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class GlobalDedupOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    duplicates: list[DuplicateDecision]


def _model_context(items: list[QuestionImportItem], s2: dict[str, Any]) -> dict[str, Any]:
    answers = [answer for unit in s2.get("units") or [] for answer in unit.get("source_answers") or []]
    return {
        "questions": [{"item_id": str(item.id), "number": (item.payload.get("source") or {}).get("original_number"),
                       "page": (item.payload.get("source") or {}).get("page_start"),
                       "stem": (item.payload.get("content") or {}).get("question_text") or "",
                       "options": (item.payload.get("content") or {}).get("options") or []}
                      for item in items],
        "source_answers": [{"number": answer.get("original_number"), "page": answer.get("page_start"),
                            "question_excerpt": str(answer.get("question_excerpt") or "")[:180],
                            "answer_excerpt": str(answer.get("text") or "")[:180]}
                           for answer in answers],
    }


class QwenGlobalDedupProvider:
    def __init__(self, *, transport: httpx.BaseTransport | None = None):
        self.settings = get_settings()
        self.transport = transport

    def review(self, context: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        prompt = (
            "你负责对整份数学资料做跨页段去重。下面列出了所有提取到的候选题，另列出了资料答案。"
            "答案区可能完整重述前面的题目；确认是同一题时，即使相隔很多页，也应标为 answer_restatement，"
            "保留前面原题，删除后面的重复候选。若文件从答案解析开始、没有前面的同题，完整题目必须保留。"
            "文字格式、空格、下划线不同不代表新题。判重只决定保留和删除哪条题目候选，"
            "不得据此合并、复制或改写答案与图片；答案会另行按来源关联。"
            "另有真正重复的题目时标为 same_question。不同题目或无法确定的题目不要列入 duplicates。"
            "必须逐一比较整份列表，不要只检查相邻页或相同题号；不要编造题目、题号、答案或候选 ID。"
            "keep_item_id 和 remove_item_id 必须来自列表，每个 remove_item_id 最多出现一次；"
            "evidence_quote 摘录两题共有的关键题干，reason 简述判断依据。"
            + json.dumps(context, ensure_ascii=False)
        )
        payload = {"model": self.settings.question_import_s2_model,
                   "messages": [{"role": "user", "content": prompt}],
                   "response_format": {"type": "json_schema", "json_schema": {
                       "name": "global_question_dedup", "strict": True,
                       "schema": GlobalDedupOutput.model_json_schema()}},
                   "include_tool_usage": True,
                   "max_tokens": min(GLOBAL_DEDUPE_MAX_OUTPUT_TOKENS,
                                     self.settings.question_import_s2_max_output_tokens)}
        start = time.monotonic()
        try:
            with httpx.Client(timeout=self.settings.question_import_s2_timeout_seconds, transport=self.transport) as client:
                response = client.post(self.settings.question_import_s2_base_url.rstrip("/") + "/chat/completions",
                                       headers={"Authorization": "Bearer " + self.settings.question_import_s2_api_key},
                                       json=payload)
                response.raise_for_status()
                body = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise ImportProblem("MODEL_REQUEST_FAILED", "整批模型判重请求失败") from exc
        try:
            choice = body["choices"][0]
            if choice["finish_reason"] != "stop":
                raise ValueError("truncated")
            output = GlobalDedupOutput.model_validate_json(choice["message"]["content"])
        except (KeyError, IndexError, TypeError, ValueError, ValidationError) as exc:
            raise ImportProblem("INVALID_MODEL_OUTPUT", "整批模型判重结果无效；本次调用可能已产生费用") from exc
        ids = {question["item_id"] for question in context["questions"]}
        removed: set[str] = set()
        for decision in output.duplicates:
            if (decision.keep_item_id not in ids or decision.remove_item_id not in ids
                    or decision.keep_item_id == decision.remove_item_id
                    or decision.remove_item_id in removed):
                raise ImportProblem("INVALID_MODEL_OUTPUT", "整批模型判重引用了无效或重复的候选 ID")
            removed.add(decision.remove_item_id)
        usage = body.get("usage") or {}
        return [decision.model_dump(mode="json") for decision in output.duplicates], {
            "usage": usage, "estimated_cny": estimate_cost(usage),
            "elapsed_seconds": round(time.monotonic() - start, 3), "response_id": body.get("id")}


def dedupe_batch(db: Session, batch_id: uuid.UUID, *, budget_cny: float,
                 operator_token: str | None = None, automatic: bool = False) -> dict[str, Any]:
    settings = get_settings()
    if not automatic:
        require_s2_operator_token(operator_token)
    if (not settings.question_import_s2_enabled or not settings.question_import_s2_api_key
            or not settings.question_import_s2_base_url.startswith("https://")
            or settings.question_import_s2_model != FLASH_MODEL):
        raise ImportProblem("S2_NOT_CONFIGURED", "整批模型判重尚未配置", 409)
    if not 0 < budget_cny <= settings.question_import_s2_max_budget_cny:
        raise ImportProblem("INVALID_S2_BUDGET", "判重预算超出服务端允许范围")
    batch = db.scalar(select(QuestionImportBatch).where(
        QuestionImportBatch.id == batch_id, QuestionImportBatch.owner_scope == OWNER_SCOPE).with_for_update())
    if batch is None:
        raise ImportProblem("NOT_FOUND", "导入批次不存在", 404)
    if batch.status != "review_pending" or batch.stage != "s2_completed":
        raise ImportProblem("S3_REVIEW_UNAVAILABLE", "请先完成题目识别", 409)
    items = list(db.scalars(select(QuestionImportItem).where(
        QuestionImportItem.batch_id == batch_id).order_by(QuestionImportItem.ordinal).with_for_update()))
    checkpoint = dict(batch.checkpoint or {})
    s3 = dict(checkpoint.get("s3") or {})
    prior = s3.get("global_dedupe") or {}
    if prior.get("status") in {"running", "completed"}:
        return prior
    context = _model_context(items, checkpoint.get("s2") or {})
    if len(items) < 2:
        if batch.source_mode == "combined":
            reconcile_source_answers(db, batch_id, force=True)
        result = {"status": "completed", "version": GLOBAL_DEDUPE_VERSION,
                  "removed_duplicate_count": 0, "reviewed_candidate_count": len(items)}
        checkpoint = dict(batch.checkpoint or {})
        s3 = dict(checkpoint.get("s3") or {})
        s3["global_dedupe"] = result
        checkpoint["s3"] = s3
        batch.checkpoint = checkpoint
        db.commit()
        return result
    if len(json.dumps(context, ensure_ascii=False)) > 150_000:
        raise ImportProblem("S3_GLOBAL_TOO_LARGE", "整批题目文字超过模型单次判重范围", 409)
    minimum_cost = (len(json.dumps(context, ensure_ascii=False)) / 2
                    * settings.question_import_s2_input_million_price_cny / 1_000_000
                    + min(GLOBAL_DEDUPE_MAX_OUTPUT_TOKENS, settings.question_import_s2_max_output_tokens)
                    * settings.question_import_s2_output_million_price_cny / 1_000_000)
    spent = _batch_review_spent(s3)
    if spent + minimum_cost > budget_cny:
        raise ImportProblem("S3_REVIEW_BUDGET", "整批判重预算不足", 409)
    s3["global_dedupe"] = {"status": "running", "version": GLOBAL_DEDUPE_VERSION,
                           "started_at": datetime.now(timezone.utc).isoformat(),
                           "reserved_cny": round(minimum_cost, 6)}
    checkpoint["s3"] = s3
    batch.checkpoint = checkpoint
    db.commit()
    try:
        decisions, meta = QwenGlobalDedupProvider().review(context)
        result: dict[str, Any] = {"status": "completed", "version": GLOBAL_DEDUPE_VERSION,
                                  "decisions": decisions, "reviewed_candidate_count": len(items),
                                  "reserved_cny": round(minimum_cost, 6), **meta}
    except ImportProblem as exc:
        result = {"status": "failed", "version": GLOBAL_DEDUPE_VERSION,
                  "reserved_cny": round(minimum_cost, 6),
                  "error_code": exc.code, "error_message": str(exc), "cost_may_have_occurred": True}
    except Exception:
        result = {"status": "failed", "version": GLOBAL_DEDUPE_VERSION,
                  "reserved_cny": round(minimum_cost, 6),
                  "error_code": "S3_GLOBAL_FAILED", "error_message": "整批判重失败；本次调用可能已产生费用",
                  "cost_may_have_occurred": True}
    batch = db.scalar(select(QuestionImportBatch).where(QuestionImportBatch.id == batch_id).with_for_update())
    checkpoint = dict(batch.checkpoint or {})
    s3 = dict(checkpoint.get("s3") or {})
    s3["global_dedupe"] = result
    checkpoint["s3"] = s3
    batch.checkpoint = checkpoint
    removed_count = 0
    if result["status"] == "completed":
        for decision in result["decisions"]:
            issue = {"key": "global:" + decision["remove_item_id"], "kind": "overlap",
                     "content": {"other_item_id": decision["keep_item_id"],
                                 "candidate_item_id": decision["remove_item_id"]}}
            original = next(item for item in items if str(item.id) == decision["remove_item_id"])
            advice = {"verdict": "same_question",
                      "source_page": (original.payload.get("source") or {}).get("page_start") or 1,
                      "evidence_quote": decision["evidence_quote"], "reason": decision["reason"]}
            removed_count += _apply_overlap_advice(db, batch, issue, advice,
                                                   keep_item_id=decision["keep_item_id"])
        if batch.source_mode == "combined":
            reconcile_source_answers(db, batch_id, force=True)
    result = {**result, "removed_duplicate_count": removed_count}
    checkpoint = dict(batch.checkpoint or {})
    s3 = dict(checkpoint.get("s3") or {})
    s3["global_dedupe"] = result
    checkpoint["s3"] = s3
    batch.checkpoint = checkpoint
    db.commit()
    return result


def process_global_dedupe_one() -> bool:
    """Finish a newly started S2 batch without requiring a second button click."""
    with SessionLocal() as db:
        candidates = list(db.scalars(select(QuestionImportBatch).where(
            QuestionImportBatch.owner_scope == OWNER_SCOPE,
            QuestionImportBatch.status == "review_pending",
            QuestionImportBatch.stage == "s2_completed",
        ).order_by(QuestionImportBatch.created_at).with_for_update(skip_locked=True)))
        def eligible(row: QuestionImportBatch) -> bool:
            if not ((row.checkpoint or {}).get("s2") or {}).get("auto_global_dedupe"):
                return False
            prior = ((row.checkpoint or {}).get("s3") or {}).get("global_dedupe") or {}
            if not prior:
                return True
            if prior.get("status") != "auto_claimed":
                return False
            try:
                claimed_at = datetime.fromisoformat(prior["started_at"])
                return claimed_at < datetime.now(timezone.utc) - timedelta(minutes=10)
            except (KeyError, TypeError, ValueError):
                return True

        batch = next((row for row in candidates if eligible(row)), None)
        if batch is None:
            return False
        batch_id = batch.id
        s2 = (batch.checkpoint or {}).get("s2") or {}
        remaining = float(s2.get("budget_cny") or 0) - float(s2.get("estimated_cny") or 0)
        if s2.get("cost_unknown"):
            remaining = 0
        checkpoint = dict(batch.checkpoint or {})
        s3 = dict(checkpoint.get("s3") or {})
        s3["global_dedupe"] = {"status": "auto_claimed", "version": GLOBAL_DEDUPE_VERSION,
                               "started_at": datetime.now(timezone.utc).isoformat()}
        checkpoint["s3"] = s3
        batch.checkpoint = checkpoint
        db.commit()
    try:
        if remaining <= 0:
            raise ImportProblem("S3_REVIEW_BUDGET", "当前预算已用完，请增加预算后手动整理整批", 409)
        with SessionLocal() as db:
            dedupe_batch(db, batch_id, budget_cny=remaining, automatic=True)
    except ImportProblem as exc:
        status = "pending_budget" if exc.code in {"S3_REVIEW_BUDGET", "INVALID_S2_BUDGET"} else "failed"
        with SessionLocal() as db:
            batch = db.get(QuestionImportBatch, batch_id)
            if batch is not None:
                checkpoint = dict(batch.checkpoint or {})
                s3 = dict(checkpoint.get("s3") or {})
                s3["global_dedupe"] = {"status": status, "version": GLOBAL_DEDUPE_VERSION,
                                       "error_code": exc.code, "error_message": str(exc)}
                checkpoint["s3"] = s3
                batch.checkpoint = checkpoint
                db.commit()
    except Exception:
        logger.exception("Automatic whole-batch deduplication failed for %s", batch_id)
        with SessionLocal() as db:
            batch = db.get(QuestionImportBatch, batch_id)
            if batch is not None:
                checkpoint = dict(batch.checkpoint or {})
                s3 = dict(checkpoint.get("s3") or {})
                s3["global_dedupe"] = {"status": "failed", "version": GLOBAL_DEDUPE_VERSION,
                                       "error_code": "S3_GLOBAL_FAILED",
                                       "error_message": "整批自动判重失败，请手动重试"}
                checkpoint["s3"] = s3
                batch.checkpoint = checkpoint
                db.commit()
    return True
