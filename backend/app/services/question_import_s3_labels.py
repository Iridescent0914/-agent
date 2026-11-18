"""Resumable, teacher-unconfirmed label suggestions for deduplicated candidates."""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import time
import uuid
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.models import KnowledgePoint, QuestionImportBatch, QuestionImportItem
from app.services.knowledge_catalog import CATALOG_PATH, formal_kps_by_code, formal_literacy_dimensions
from app.services.question_import import OWNER_SCOPE, ImportProblem, active_candidate, require_s2_operator_token
from app.services.question_import_s2 import FLASH_MODEL
from app.services.question_import_s3_images import image_bytes


LABEL_VERSION = "s3-label-suggestions-v2"
GROUP_SIZE = 12
MAX_GROUP_CHARACTERS = 24_000
MAX_IMAGES_PER_GROUP = 12
logger = logging.getLogger(__name__)


class LabelSuggestion(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    item_id: str
    question_type: Literal["choice", "fill_blank", "calculation", "solution", "true_false", "unknown"]
    difficulty: int | None
    main_kp_code: str | None
    alternative_kp_codes: list[str]
    uncertainty: str | None


class LabelOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    suggestions: list[LabelSuggestion]


def _allowed_kps(db: Session) -> dict[str, dict[str, Any]]:
    existing = set(db.scalars(select(KnowledgePoint.code)))
    literacies = set(formal_literacy_dimensions())
    return {code: point for code, point in formal_kps_by_code().items()
            if code in existing and point.get("available_for_import", True)
            and point.get("core_competencies")
            and set(point["core_competencies"]) <= literacies}


def _fingerprint(item: QuestionImportItem) -> str:
    content = item.payload.get("content") or {}
    material = {"question_text": content.get("question_text"), "options": content.get("options"),
                "asset_ids": content.get("asset_ids"), "subquestions":
                    (item.payload.get("extraction") or {}).get("subquestions")}
    return hashlib.sha256(json.dumps(material, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def _groups(items: list[QuestionImportItem]) -> list[list[dict[str, Any]]]:
    groups: list[list[dict[str, Any]]] = []
    group: list[dict[str, Any]] = []
    size = images = 0
    for item in items:
        content = item.payload.get("content") or {}
        entry = {"item_id": str(item.id), "revision": item.revision, "fingerprint": _fingerprint(item),
                 "question_text": content.get("question_text") or "",
                 "options": content.get("options") or [],
                 "subquestions": (item.payload.get("extraction") or {}).get("subquestions") or [],
                 "asset_ids": content.get("asset_ids") or []}
        length = len(json.dumps(entry, ensure_ascii=False))
        count = len(entry["asset_ids"])
        if length > MAX_GROUP_CHARACTERS or count > MAX_IMAGES_PER_GROUP:
            raise ImportProblem("S3_LABEL_INPUT_TOO_LARGE", "单题内容或图片过多，请先人工整理", 409)
        if group and (len(group) >= GROUP_SIZE or size + length > MAX_GROUP_CHARACTERS
                      or images + count > MAX_IMAGES_PER_GROUP):
            groups.append(group)
            group, size, images = [], 0, 0
        group.append(entry)
        size += length
        images += count
    if group:
        groups.append(group)
    return groups


def _split_failed_group(state: dict[str, Any]) -> bool:
    """A teacher-triggered retry reduces a paid invalid group's size once."""
    if state.get("error_code") != "S3_LABEL_OUTPUT_INVALID":
        return False
    group = next((row for row in state.get("groups") or [] if row["status"] == "pending"), None)
    if group is None or len(group["items"]) < 2:
        return False
    middle = len(group["items"]) // 2
    next_index = max(row["index"] for row in state["groups"]) + 1
    group["status"] = "superseded"
    for offset, entries in enumerate((group["items"][:middle], group["items"][middle:])):
        state["groups"].append({"index": next_index + offset, "status": "pending", "items": entries})
    return True


def _text_cost(usage: dict[str, Any]) -> float | None:
    settings = get_settings()
    try:
        prompt = int(usage["prompt_tokens"])
        completion = int(usage["completion_tokens"])
        if min(prompt, completion) < 0:
            return None
    except (KeyError, TypeError, ValueError):
        return None
    return round((prompt * settings.question_import_s2_input_million_price_cny
                  + completion * settings.question_import_s2_output_million_price_cny) / 1_000_000, 6)


def validate_suggestions(value: object, group: list[dict[str, Any]], allowed: dict[str, dict]) -> list[dict]:
    try:
        output = LabelOutput.model_validate(value)
    except ValidationError as exc:
        raise ImportProblem("S3_LABEL_OUTPUT_INVALID", "标签模型响应结构无效") from exc
    expected = {entry["item_id"] for entry in group}
    actual = [row.item_id for row in output.suggestions]
    if len(actual) != len(expected) or set(actual) != expected:
        raise ImportProblem("S3_LABEL_OUTPUT_INVALID", "标签模型未逐题返回或引用了错误候选")
    for row in output.suggestions:
        codes = [*([row.main_kp_code] if row.main_kp_code else []), *row.alternative_kp_codes]
        if (len(codes) != len(set(codes)) or len(row.alternative_kp_codes) > 2
                or any(code not in allowed for code in codes)
                or row.difficulty is not None and not 1 <= row.difficulty <= 5
                or row.main_kp_code is None and not row.uncertainty):
            raise ImportProblem("S3_LABEL_OUTPUT_INVALID", "标签模型给出非法或不完整的知识点建议")
    return [row.model_dump(mode="json") for row in output.suggestions]


class QwenLabelProvider:
    def __init__(self, *, transport: httpx.BaseTransport | None = None):
        self.settings = get_settings()
        self.transport = transport
        self.last_response: dict[str, Any] | None = None

    def suggest(self, group: list[dict[str, Any]], allowed: dict[str, dict],
                images: dict[str, list[tuple[str, bytes]]]) -> tuple[list[dict], dict]:
        catalog = [{"code": code, "name": point["name"], "section": point["section"],
                    "chapter": point["chapter_name"], "grade": point["grade"]}
                   for code, point in allowed.items()]
        questions = [{key: value for key, value in entry.items()
                      if key in {"item_id", "question_text", "options", "subquestions", "asset_ids"}}
                     for entry in group]
        prompt = (
            "你是初中数学题目标签建议员。只处理给定题目，逐题返回。题型只能是 choice、fill_blank、"
            "calculation、solution、true_false 或 unknown；难度为1到5星，无法判断填null。"
            "主属知识点只能从开放目录选一个 code；最多另给两个不同备选 code。"
            "若题目超出目录范围、题干残缺、关键题图缺失或依据不足，主属知识点填null，"
            "在 uncertainty 中说明原因。不要根据文件名或题号推断知识点。"
            "不要输出章、节、年级、核心素养或必做标记；这些由系统或教师决定。"
            "图片按相邻的 item_id 和 asset_id 对应题目。"
            f"本次共有 {len(group)} 道题，suggestions 必须恰好返回 {len(group)} 条，"
            "以下 item_id 均须且只须出现一次："
            + json.dumps([entry["item_id"] for entry in group], ensure_ascii=False)
            + "。开放目录：" + json.dumps(catalog, ensure_ascii=False) + "。题目："
            + json.dumps(questions, ensure_ascii=False)
        )
        content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
        for entry in group:
            for asset_id, png in images.get(entry["item_id"], []):
                content.append({"type": "text", "text": f"题目 {entry['item_id']} 的图片 {asset_id}"})
                content.append({"type": "image_url", "image_url": {
                    "url": "data:image/png;base64," + base64.b64encode(png).decode("ascii")}})
        body = {"model": self.settings.question_import_s2_model,
                "messages": [{"role": "user", "content": content}],
                "response_format": {"type": "json_schema", "json_schema": {
                    "name": "question_label_suggestions", "strict": True,
                    "schema": LabelOutput.model_json_schema()}},
                "include_tool_usage": True,
                "max_tokens": min(8192, self.settings.question_import_s2_max_output_tokens)}
        started = time.monotonic()
        try:
            with httpx.Client(timeout=self.settings.question_import_s2_timeout_seconds,
                              transport=self.transport) as client:
                response = client.post(self.settings.question_import_s2_base_url.rstrip("/") + "/chat/completions",
                                       headers={"Authorization": "Bearer " + self.settings.question_import_s2_api_key},
                                       json=body)
                response.raise_for_status()
                raw = response.json()
                self.last_response = raw
        except (httpx.HTTPError, ValueError) as exc:
            raise ImportProblem("MODEL_REQUEST_FAILED", "标签建议请求失败；本次调用可能已产生费用") from exc
        try:
            choice = raw["choices"][0]
            if choice["finish_reason"] != "stop":
                raise ValueError("truncated")
            value = json.loads(choice["message"]["content"])
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise ImportProblem("S3_LABEL_OUTPUT_INVALID", "标签模型响应不完整；本次调用可能已产生费用") from exc
        suggestions = validate_suggestions(value, group, allowed)
        usage = raw.get("usage") or {}
        return suggestions, {"usage": usage, "estimated_cny": _text_cost(usage),
                             "elapsed_seconds": round(time.monotonic() - started, 3),
                             "response_id": raw.get("id")}


def start_labels(db: Session, batch_id: uuid.UUID, *, budget_cny: float,
                 operator_token: str | None = None, rerun: bool = False) -> QuestionImportBatch:
    require_s2_operator_token(operator_token)
    settings = get_settings()
    if (not settings.question_import_s2_enabled or not settings.question_import_s2_api_key
            or not settings.question_import_s2_base_url.startswith("https://")
            or settings.question_import_s2_model != FLASH_MODEL):
        raise ImportProblem("S3_LABEL_NOT_CONFIGURED", "标签建议模型尚未配置", 409)
    if not 0 < budget_cny <= settings.question_import_s2_max_budget_cny:
        raise ImportProblem("INVALID_S3_BUDGET", "标签建议预算超出允许范围")
    batch = db.scalar(select(QuestionImportBatch).where(
        QuestionImportBatch.id == batch_id, QuestionImportBatch.owner_scope == OWNER_SCOPE).with_for_update())
    if batch is None:
        raise ImportProblem("NOT_FOUND", "导入批次不存在", 404)
    if batch.status != "review_pending" or batch.stage != "s2_completed":
        raise ImportProblem("S3_LABEL_UNAVAILABLE", "请先完成题目识别", 409)
    s3 = (batch.checkpoint or {}).get("s3") or {}
    if (s3.get("global_dedupe") or {}).get("status") != "completed":
        raise ImportProblem("S3_DEDUPE_PENDING", "请先完成整批去重", 409)
    if batch.source_mode == "separate" and (s3.get("separate_answers") or {}).get("status") != "completed":
        raise ImportProblem("S3_ANSWERS_PENDING", "请先完成答案文件识别和关联", 409)
    if batch.source_mode in {"combined", "separate"} and not s3.get("source_answer_reconciliation"):
        raise ImportProblem("S3_ANSWERS_PENDING", "请先完成来源答案关联", 409)
    if batch.source_mode in {"combined", "separate"}:
        matching = s3.get("answer_model_matching") or {}
        authorized = ((batch.checkpoint or {}).get("s2") or {}).get("auto_answer_matching") if batch.source_mode == "combined" else (
            (s3.get("separate_answers") or {}).get("auto_answer_matching"))
        if (authorized or matching) and matching.get("status") != "completed":
            raise ImportProblem("S3_MATCH_PENDING", "请先完成模型辅助配对或处理预算与失败状态", 409)
    if batch.catalog_snapshot_sha256 != hashlib.sha256(CATALOG_PATH.read_bytes()).hexdigest():
        raise ImportProblem("CATALOG_CHANGED", "正式标签目录已变化，请重新上传批次", 409)
    if batch.files_purged_at or batch.expires_at <= datetime.now(timezone.utc):
        raise ImportProblem("FILE_EXPIRED", "原文件和题图已过期，请重新上传", 409)
    prior = s3.get("labels") or {}
    if prior.get("groups") and prior.get("version") != LABEL_VERSION:
        raise ImportProblem("S3_LABEL_MIGRATION_REQUIRED", "旧标签分组尚未迁移，请先完成数据库升级", 409)
    if rerun:
        if prior.get("status") != "completed":
            raise ImportProblem("S3_LABEL_UNAVAILABLE", "请先完成当前整批标签任务", 409)
    elif prior.get("status") in {"queued", "processing", "completed"}:
        raise ImportProblem("COMMIT_CONFLICT", "标签任务已排队、进行中或完成", 409)
    if prior.get("cost_unknown"):
        raise ImportProblem("S3_LABEL_COST_UNKNOWN", "上次调用用量未知，请先核对账单", 409)
    if budget_cny <= float(prior.get("estimated_cny") or 0):
        raise ImportProblem("INVALID_S3_BUDGET", "新预算须高于已记录费用")
    if not _allowed_kps(db):
        raise ImportProblem("TAG_OUT_OF_SCOPE", "当前没有可入库的正式知识点", 409)
    items = [item for item in db.scalars(select(QuestionImportItem).where(
        QuestionImportItem.batch_id == batch_id).order_by(QuestionImportItem.ordinal)) if active_candidate(item)]
    if any(item.is_fake for item in items):
        raise ImportProblem("S3_LABEL_UNAVAILABLE", "模拟候选题不能调用正式标签模型", 409)
    groups = _groups(items) if rerun or not prior.get("groups") else []
    checkpoint = dict(batch.checkpoint or {})
    s3 = dict(checkpoint.get("s3") or {})
    state = deepcopy(s3.get("labels") or {})
    if rerun:
        if state.get("groups"):
            s3.setdefault("label_group_audit", []).append({
                "version": state.get("version"), "groups": state["groups"],
                "calls": state.get("calls"), "estimated_cny": state.get("estimated_cny"),
            })
        state.pop("skipped_item_ids", None)
        state["overwrite_manual_tags"] = True
        state["groups"] = [{"index": index, "status": "pending", "items": group}
                           for index, group in enumerate(groups)]
    elif not state.get("groups"):
        state["overwrite_manual_tags"] = False
        state["groups"] = [{"index": index, "status": "pending", "items": group}
                           for index, group in enumerate(groups)]
    else:
        _split_failed_group(state)
    has_pending = any(row["status"] == "pending" for row in state["groups"])
    state.update({"status": "queued" if has_pending else "completed", "version": LABEL_VERSION,
                  "budget_cny": budget_cny, "model": settings.question_import_s2_model,
                  "catalog_snapshot_sha256": batch.catalog_snapshot_sha256})
    state.setdefault("calls", 0)
    state.setdefault("estimated_cny", 0.0)
    state.pop("error_code", None)
    state.pop("error_message", None)
    state.pop("cost_unknown", None)
    s3["labels"] = state
    checkpoint["s3"] = s3
    batch.checkpoint = checkpoint
    return batch


def _apply_suggestions(db: Session, batch: QuestionImportBatch, group: dict,
                       suggestions: list[dict], allowed: dict[str, dict],
                       *, overwrite_manual_tags: bool = False) -> tuple[int, list[str]]:
    rows = {str(item.id): item for item in db.scalars(select(QuestionImportItem).where(
        QuestionImportItem.batch_id == batch.id).with_for_update()) if active_candidate(item)}
    applied = 0
    skipped: list[str] = []
    by_id = {result["item_id"]: result for result in suggestions}
    for entry in group["items"]:
        result = by_id[entry["item_id"]]
        item = rows.get(entry["item_id"])
        if item is None or item.revision != entry["revision"] or _fingerprint(item) != entry["fingerprint"]:
            skipped.append(entry["item_id"])
            continue
        if (item.payload.get("teacher_decision") or "pending") != "pending":
            skipped.append(entry["item_id"])
            continue
        payload = dict(item.payload)
        tags = dict(payload.get("tags") or {})
        previous = payload.get("label_suggestion") or {}
        replace_previous = bool(previous)
        overrides = set(previous.get("teacher_overrides") or [])
        if overwrite_manual_tags or ("question_type" not in overrides and (tags.get("question_type") in {None, "unknown"} or (
                replace_previous and tags.get("question_type") == previous.get("question_type")))):
            tags["question_type"] = result["question_type"]
        if overwrite_manual_tags or ("difficulty" not in overrides and (tags.get("difficulty") is None or (
                replace_previous and tags.get("difficulty") == previous.get("difficulty")))):
            tags["difficulty"] = result["difficulty"]
        if overwrite_manual_tags or ("main_kp_code" not in overrides and (not tags.get("main_kp_code") or (
                replace_previous and tags.get("main_kp_code") == previous.get("main_kp_code")))):
            if result["main_kp_code"]:
                kp = allowed[result["main_kp_code"]]
                tags.update({"main_kp_code": result["main_kp_code"], "grade": kp["grade"],
                             "chapter_code": kp["chapter_code"], "section": kp["section"],
                             "core_literacies": kp["core_competencies"]})
            else:
                tags.update({"main_kp_code": None, "grade": None, "chapter_code": None,
                             "section": None, "core_literacies": []})
        payload["tags"] = tags
        payload["label_status"] = "needs_confirmation" if tags.get("main_kp_code") else "out_of_scope"
        payload["label_suggestion"] = {**result, "model": get_settings().question_import_s2_model,
                                        "version": LABEL_VERSION, "revision": item.revision + 1,
                                        "fingerprint": entry["fingerprint"],
                                        "catalog_snapshot_sha256": batch.catalog_snapshot_sha256,
                                        "teacher_overrides": [] if overwrite_manual_tags else sorted(overrides),
                                        "stale": False}
        item.revision += 1
        payload["revision"] = item.revision
        item.payload = payload
        applied += 1
    return applied, skipped


def process_labels_one() -> bool:
    with SessionLocal() as db:
        rows = list(db.scalars(select(QuestionImportBatch).where(
            QuestionImportBatch.owner_scope == OWNER_SCOPE,
            QuestionImportBatch.status == "review_pending",
            QuestionImportBatch.stage == "s2_completed").order_by(QuestionImportBatch.created_at)
            .with_for_update(skip_locked=True)))
        now = datetime.now(timezone.utc)
        timeout = timedelta(seconds=get_settings().question_import_s2_timeout_seconds + 120)

        def runnable(row: QuestionImportBatch) -> bool:
            state = ((row.checkpoint or {}).get("s3") or {}).get("labels") or {}
            if state.get("status") == "queued":
                return True
            if state.get("status") != "processing":
                return False
            try:
                return datetime.fromisoformat(state["started_at"]) <= now - timeout
            except (KeyError, ValueError, TypeError):
                return True

        batch = next((row for row in rows if runnable(row)), None)
        if batch is None:
            return False
        batch_id = batch.id
        checkpoint = dict(batch.checkpoint or {})
        s3 = dict(checkpoint.get("s3") or {})
        state = deepcopy(s3["labels"])
        if state["status"] == "processing":
            try:
                started = datetime.fromisoformat(state["started_at"])
                if started > datetime.now(timezone.utc) - timedelta(seconds=get_settings().question_import_s2_timeout_seconds + 120):
                    return False
            except (KeyError, ValueError, TypeError):
                pass
            state.update({"status": "paused", "cost_unknown": True,
                          "error_code": "S3_LABEL_COST_UNKNOWN",
                          "error_message": "标签调用中断，用量未确认；请先核对账单"})
            s3["labels"] = state
            checkpoint["s3"] = s3
            batch.checkpoint = checkpoint
            db.commit()
            return True
        group = next((row for row in state["groups"] if row["status"] == "pending"), None)
        if group is None:
            state["status"] = "completed"
            s3["labels"] = state
            checkpoint["s3"] = s3
            batch.checkpoint = checkpoint
            db.commit()
            return True
        current_items = {str(item.id): item for item in db.scalars(select(QuestionImportItem).where(
            QuestionImportItem.batch_id == batch_id).with_for_update()) if active_candidate(item)}
        active_items = [entry for entry in group["items"]
                        if (item := current_items.get(entry["item_id"])) is not None
                        and item.revision == entry["revision"]
                        and _fingerprint(item) == entry["fingerprint"]
                        and (item.payload.get("teacher_decision") or "pending") == "pending"]
        skipped_ids = [entry["item_id"] for entry in group["items"] if entry not in active_items]
        if not active_items:
            group.update({"status": "completed", "applied_count": 0, "skipped_item_ids": skipped_ids})
            state["status"] = "completed" if not any(row["status"] == "pending" for row in state["groups"]) else "queued"
            s3["labels"] = state
            checkpoint["s3"] = s3
            batch.checkpoint = checkpoint
            db.commit()
            return True
        if float(state["estimated_cny"]) >= float(state["budget_cny"]):
            state.update({"status": "paused", "error_code": "S3_LABEL_BUDGET",
                          "error_message": "标签建议预算已用完，请增加预算后继续"})
            s3["labels"] = state
            checkpoint["s3"] = s3
            batch.checkpoint = checkpoint
            db.commit()
            return True
        state.update({"status": "processing", "started_at": datetime.now(timezone.utc).isoformat()})
        s3["labels"] = state
        checkpoint["s3"] = s3
        batch.checkpoint = checkpoint
        db.commit()
        group = {**group, "items": active_items, "skipped_item_ids": skipped_ids}
        allowed = _allowed_kps(db)
        if batch.files_purged_at or batch.expires_at <= datetime.now(timezone.utc):
            failure = ImportProblem("FILE_EXPIRED", "原文件和题图已过期，请重新上传", 409)
        elif batch.catalog_snapshot_sha256 != hashlib.sha256(CATALOG_PATH.read_bytes()).hexdigest():
            failure = ImportProblem("CATALOG_CHANGED", "正式标签目录已变化，请重新上传批次", 409)
        else:
            failure = None
        try:
            images = {entry["item_id"]: [(asset_id, image_bytes(db, batch_id, uuid.UUID(asset_id)))
                                           for asset_id in entry["asset_ids"]]
                      for entry in group["items"]}
        except (ImportProblem, ValueError) as exc:
            failure = ImportProblem("S3_LABEL_IMAGE_MISSING", "候选题图片不可用，请重新上传或核对资产", 409)
            images = {}
    provider = QwenLabelProvider()
    try:
        if failure:
            raise failure
        suggestions, meta = provider.suggest(group["items"], allowed, images)
        suggestions = validate_suggestions({"suggestions": suggestions}, group["items"], allowed)
    except Exception as exc:
        if not isinstance(exc, ImportProblem):
            logger.exception("S3 label worker failed for batch %s", batch_id)
            exc = ImportProblem("S3_LABEL_WORKER_FAILED", "标签任务处理失败；请检查服务端日志")
        raw = getattr(provider, "last_response", None)
        usage = (raw.get("usage") or {}) if isinstance(raw, dict) else {}
        known_cost = _text_cost(usage) if usage else None
        with SessionLocal() as db:
            batch = db.scalar(select(QuestionImportBatch).where(
                QuestionImportBatch.id == batch_id).with_for_update())
            if batch is not None:
                checkpoint = dict(batch.checkpoint or {})
                s3 = dict(checkpoint.get("s3") or {})
                state = deepcopy(s3["labels"])
                state["calls"] += 0 if failure else 1
                if known_cost is not None:
                    state["estimated_cny"] = round(float(state["estimated_cny"]) + known_cost, 6)
                    state["groups"][group["index"]]["usage"] = usage
                    state["groups"][group["index"]]["response_id"] = raw.get("id")
                if isinstance(raw, dict):
                    raw_text = json.dumps(raw, ensure_ascii=False)
                    state["groups"][group["index"]]["raw_response_excerpt"] = raw_text[:64_000]
                    state["groups"][group["index"]]["raw_response_truncated"] = len(raw_text) > 64_000
                state.update({"status": "failed", "error_code": exc.code,
                              "error_message": str(exc),
                              "cost_unknown": not bool(failure) and known_cost is None})
                s3["labels"] = state
                checkpoint["s3"] = s3
                batch.checkpoint = checkpoint
                db.commit()
        return True
    with SessionLocal() as db:
        batch = db.scalar(select(QuestionImportBatch).where(
            QuestionImportBatch.id == batch_id).with_for_update())
        if batch is None:
            return True
        checkpoint = dict(batch.checkpoint or {})
        s3 = dict(checkpoint.get("s3") or {})
        state = deepcopy(s3["labels"])
        unit = next(row for row in state["groups"] if row["index"] == group["index"])
        if unit["status"] == "completed":
            return True
        applied, late_skipped = _apply_suggestions(
            db, batch, group, suggestions, allowed,
            overwrite_manual_tags=bool(state.get("overwrite_manual_tags")))
        unit.update({"status": "completed", "suggestions": suggestions,
                     "applied_count": applied,
                     "skipped_item_ids": [*group["skipped_item_ids"], *late_skipped], **meta})
        state["calls"] += 1
        if meta.get("estimated_cny") is None:
            state.update({"status": "paused", "cost_unknown": True,
                          "error_code": "S3_LABEL_COST_UNKNOWN",
                          "error_message": "本组用量未知，请核对账单后重新上传"})
        else:
            state["estimated_cny"] = round(float(state["estimated_cny"]) + meta["estimated_cny"], 6)
            state["status"] = "completed" if not any(row["status"] == "pending" for row in state["groups"]) else "queued"
        s3["labels"] = state
        checkpoint["s3"] = s3
        batch.checkpoint = checkpoint
        db.commit()
    return True
