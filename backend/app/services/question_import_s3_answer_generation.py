"""Explicit, resumable answer generation for question-only import batches."""

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
from app.models import QuestionImportBatch, QuestionImportItem
from app.services.question_import import OWNER_SCOPE, ImportProblem, active_candidate, require_s2_operator_token
from app.services.question_import_s2 import FLASH_MODEL
from app.services.question_import_s3_images import image_bytes
from app.services.question_import_s3_labels import _text_cost


ANSWER_GENERATION_VERSION = "s3-answer-generation-v4"
GROUP_SIZE = 8
MAX_GROUP_CHARACTERS = 24_000
MAX_IMAGES_PER_GROUP = 8
MAX_OUTPUT_TOKENS = 8192
logger = logging.getLogger(__name__)


class GeneratedAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    item_id: str
    status: Literal["answered", "uncertain"]
    answer_text: str | None
    uncertainty: str | None


class GeneratedAnswerOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    answers: list[GeneratedAnswer]


def _fingerprint(item: QuestionImportItem) -> str:
    content = item.payload.get("content") or {}
    value = {"question_text": content.get("question_text"), "options": content.get("options"),
             "asset_ids": content.get("asset_ids"),
             "subquestions": (item.payload.get("extraction") or {}).get("subquestions"),
             "diagram_clues": (item.payload.get("extraction") or {}).get("diagram_clues"),
             "figure_status": (item.payload.get("extraction") or {}).get("figure_status")}
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _eligible(item: QuestionImportItem) -> bool:
    payload = item.payload or {}
    return (active_candidate(item) and not item.is_fake
            and payload.get("teacher_decision", "pending") == "pending"
            and not payload.get("answers") and not payload.get("selected_answer_id"))


def _groups(items: list[QuestionImportItem]) -> list[dict[str, Any]]:
    groups: list[dict[str, Any]] = []
    current: list[dict[str, Any]] = []
    characters = images = 0
    for item in items:
        content = item.payload.get("content") or {}
        entry = {"item_id": str(item.id), "revision": item.revision, "fingerprint": _fingerprint(item),
                 "question_text": content.get("question_text") or "", "options": content.get("options") or [],
                 "subquestions": (item.payload.get("extraction") or {}).get("subquestions") or [],
                 "diagram_clues": (item.payload.get("extraction") or {}).get("diagram_clues") or [],
                 "figure_status": (item.payload.get("extraction") or {}).get("figure_status"),
                 "asset_ids": content.get("asset_ids") or []}
        length = len(json.dumps(entry, ensure_ascii=False))
        count = len(entry["asset_ids"])
        if length > MAX_GROUP_CHARACTERS or count > MAX_IMAGES_PER_GROUP:
            raise ImportProblem("S3_ANSWER_INPUT_TOO_LARGE", "单题内容或图片过多，请先人工整理", 409)
        if current and (len(current) >= GROUP_SIZE or characters + length > MAX_GROUP_CHARACTERS
                        or images + count > MAX_IMAGES_PER_GROUP):
            groups.append({"index": len(groups), "status": "pending", "items": current})
            current, characters, images = [], 0, 0
        current.append(entry)
        characters += length
        images += count
    if current:
        groups.append({"index": len(groups), "status": "pending", "items": current})
    return groups


def validate_answers(value: object, group: list[dict[str, Any]]) -> list[dict[str, Any]]:
    try:
        output = GeneratedAnswerOutput.model_validate(value)
    except ValidationError as exc:
        raise ImportProblem("S3_ANSWER_OUTPUT_INVALID", "答案生成模型响应结构无效") from exc
    expected = {entry["item_id"] for entry in group}
    actual = [row.item_id for row in output.answers]
    if len(actual) != len(expected) or set(actual) != expected:
        raise ImportProblem("S3_ANSWER_OUTPUT_INVALID", "答案生成模型未逐题返回或引用了错误候选")
    for row in output.answers:
        if row.status == "answered":
            if not row.answer_text or not row.answer_text.strip() or len(row.answer_text) > 30_000:
                raise ImportProblem("S3_ANSWER_OUTPUT_INVALID", "模型给出的答案为空或过长")
        elif row.answer_text is not None or not row.uncertainty or not row.uncertainty.strip():
            raise ImportProblem("S3_ANSWER_OUTPUT_INVALID", "无法确定答案时须说明原因且不得猜测答案")
    return [row.model_dump(mode="json") for row in output.answers]


class QwenAnswerGenerationProvider:
    def __init__(self, *, transport: httpx.BaseTransport | None = None):
        self.settings = get_settings()
        self.transport = transport
        self.last_response: dict[str, Any] | None = None

    def generate(self, group: list[dict[str, Any]], images: dict[str, list[tuple[str, bytes]]]
                 ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        questions = [{key: value for key, value in entry.items()
                      if key in {"item_id", "question_text", "options", "subquestions",
                                 "diagram_clues", "figure_status", "asset_ids"}}
                     for entry in group]
        prompt = (
            "你为初中数学题目提供供教师核对的参考答案。只处理给定题目，逐题返回。"
            "以每题的 question_text、options 和实际附带的题图为准；diagram_clues 仅是辅助线索，"
            "不能代替题干、选项或题图。不要补写题目条件、图形信息或选项内容。"
            "先按原文理解数学符号，再求解并核对每个选项；保留数字、正负号、括号、分数、单位、"
            "不等号及上下标的含义。^n 表示 n 次方，_n 表示下标，例如 10^8 是十的八次方，"
            "不能读成 108；常见的等价数学写法不应仅因排版形式不同判为歧义。"
            "选择题返回前逐字核对所选选项的 key 和 text，确认它与求解结果一致；"
            "不要把自己改写或省略后的选项当作原文。若发现题目或选项确有歧义，"
            "uncertainty 应引用实际收到的原文，并说明歧义如何影响答案。"
            "判断条件是否缺失前，先检查 options 是否已经给出所需数据；选项本身也是题目数据。"
            "例如题目让比较若干气温、数值或算式的大小，而这些值已列在选项中，"
            "应直接比较选项，不要因题干没有重复列出这些值或没有题图就判定无法作答。"
            "answer_text 只写最终答案，不写解题过程或解析。"
            "选择题只写选项编号，如 C 或 A、C，不要重复选项内容；"
            "填空题按空顺序列出，多小题按原编号逐项列出。"
            "仅在题干残缺、解题必需的题图缺失、条件不足、选项均不符合或单选题多个选项都符合，"
            "以及确实无法可靠求解时，返回 status=uncertain、answer_text=null，"
            "并在 uncertainty 说明具体原因；能从现有信息唯一确定答案时返回 status=answered。"
            "不要猜测，也不要把模型答案当作教师确认。图片按相邻的 item_id 和 asset_id 对应题目。"
            f"本次共有 {len(group)} 道题，answers 必须恰好返回 {len(group)} 条；item_id 均须且只须出现一次："
            + json.dumps([entry["item_id"] for entry in group], ensure_ascii=False)
            + "。题目：" + json.dumps(questions, ensure_ascii=False)
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
                    "name": "question_import_generated_answers", "strict": True,
                    "schema": GeneratedAnswerOutput.model_json_schema()}},
                "include_tool_usage": True,
                "max_tokens": min(MAX_OUTPUT_TOKENS, self.settings.question_import_s2_max_output_tokens)}
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
            raise ImportProblem("MODEL_REQUEST_FAILED", "答案生成请求失败；本次调用可能已产生费用") from exc
        try:
            choice = raw["choices"][0]
            if choice["finish_reason"] != "stop":
                raise ValueError("truncated")
            value = json.loads(choice["message"]["content"])
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise ImportProblem("S3_ANSWER_OUTPUT_INVALID", "答案生成模型响应不完整；本次调用可能已产生费用") from exc
        answers = validate_answers(value, group)
        usage = raw.get("usage") or {}
        return answers, {"usage": usage, "estimated_cny": _text_cost(usage),
                         "elapsed_seconds": round(time.monotonic() - started, 3), "response_id": raw.get("id")}


def start_answer_generation(db: Session, batch_id: uuid.UUID, *, budget_cny: float,
                            operator_token: str | None = None,
                            item_ids: list[uuid.UUID] | None = None) -> QuestionImportBatch:
    require_s2_operator_token(operator_token)
    settings = get_settings()
    if (not settings.question_import_s2_enabled or not settings.question_import_s2_api_key
            or not settings.question_import_s2_base_url.startswith("https://")
            or settings.question_import_s2_model != FLASH_MODEL):
        raise ImportProblem("S3_ANSWER_NOT_CONFIGURED", "答案生成模型尚未配置", 409)
    if not 0 < budget_cny <= settings.question_import_s2_max_budget_cny:
        raise ImportProblem("INVALID_S3_BUDGET", "答案生成预算超出允许范围")
    batch = db.scalar(select(QuestionImportBatch).where(
        QuestionImportBatch.id == batch_id, QuestionImportBatch.owner_scope == OWNER_SCOPE).with_for_update())
    if batch is None:
        raise ImportProblem("NOT_FOUND", "导入批次不存在", 404)
    if batch.source_mode != "questions_only":
        raise ImportProblem("S3_ANSWER_MODE", "仅只有题目文件的批次可以生成答案", 409)
    s3 = (batch.checkpoint or {}).get("s3") or {}
    if (batch.status != "review_pending" or batch.stage != "s2_completed"
            or (s3.get("global_dedupe") or {}).get("status") != "completed"):
        raise ImportProblem("S3_DEDUPE_PENDING", "请先完成题目识别和整批去重", 409)
    if batch.files_purged_at or batch.expires_at <= datetime.now(timezone.utc):
        raise ImportProblem("FILE_EXPIRED", "原文件和题图已过期，请重新上传", 409)
    if (s3.get("labels") or {}).get("status") in {"queued", "processing"}:
        raise ImportProblem("COMMIT_CONFLICT", "请等待标签任务完成后再生成答案", 409)
    prior = deepcopy(s3.get("answer_generation") or {})
    if prior.get("status") in {"queued", "processing"}:
        raise ImportProblem("COMMIT_CONFLICT", "答案生成任务正在进行", 409)
    if prior.get("cost_unknown"):
        raise ImportProblem("S3_ANSWER_COST_UNKNOWN", "上次调用用量未知，请先核对账单", 409)
    if budget_cny <= float(prior.get("estimated_cny") or 0):
        raise ImportProblem("INVALID_S3_BUDGET", "新预算须高于已记录费用")
    if prior.get("status") in {"failed", "paused"} and any(
            row.get("status") == "pending" for row in prior.get("groups") or []):
        if item_ids is not None:
            raise ImportProblem("S3_ANSWER_RESUME", "请先续跑未完成的答案生成任务", 409)
        groups = prior["groups"]
    else:
        items = list(db.scalars(select(QuestionImportItem).where(
            QuestionImportItem.batch_id == batch_id).order_by(QuestionImportItem.ordinal)))
        if any(item.is_fake and active_candidate(item) for item in items):
            raise ImportProblem("S3_ANSWER_UNAVAILABLE", "模拟候选题不能调用正式答案模型", 409)
        selected = set(item_ids) if item_ids is not None else None
        if selected is not None and (not selected or len(selected) != len(item_ids)
                                     or selected - {item.id for item in items}):
            raise ImportProblem("S3_ANSWER_TARGETS", "所选候选题无效或重复", 409)
        targets = [item for item in items if (selected is None or item.id in selected) and _eligible(item)]
        if selected is not None and len(targets) != len(selected):
            raise ImportProblem("S3_ANSWER_TARGETS", "所选题目已有答案、已被修改状态或不可生成", 409)
        groups = _groups(targets)
        if not groups:
            raise ImportProblem("S3_ANSWER_NO_TARGETS", "当前没有需要生成答案的候选题", 409)
        offset = 1 + max((row["index"] for row in prior.get("groups") or []), default=-1)
        for row in groups:
            row["index"] += offset
        groups = [*(prior.get("groups") or []), *groups]
    prior.update({"status": "queued", "version": ANSWER_GENERATION_VERSION, "groups": groups,
                  "budget_cny": budget_cny, "model": settings.question_import_s2_model})
    prior.setdefault("calls", 0)
    prior.setdefault("estimated_cny", 0.0)
    prior.pop("error_code", None)
    prior.pop("error_message", None)
    checkpoint = dict(batch.checkpoint or {})
    s3 = dict(checkpoint.get("s3") or {})
    s3["answer_generation"] = prior
    checkpoint["s3"] = s3
    batch.checkpoint = checkpoint
    return batch


def _apply_answers(db: Session, batch: QuestionImportBatch, group: dict[str, Any],
                   answers: list[dict[str, Any]], *, response_id: str | None) -> tuple[int, list[str]]:
    rows = {str(item.id): item for item in db.scalars(select(QuestionImportItem).where(
        QuestionImportItem.batch_id == batch.id).with_for_update())}
    by_id = {answer["item_id"]: answer for answer in answers}
    applied = 0
    skipped: list[str] = []
    for entry in group["items"]:
        item = rows.get(entry["item_id"])
        if (item is None or not _eligible(item) or item.revision != entry["revision"]
                or _fingerprint(item) != entry["fingerprint"]):
            skipped.append(entry["item_id"])
            continue
        result = by_id[entry["item_id"]]
        payload = dict(item.payload)
        if result["status"] == "answered":
            version = {"answer_id": "model-" + str(uuid.uuid4()), "answer_source": "model_generated",
                       "text": result["answer_text"].strip(), "evidence": [], "teacher_confirmed": False,
                       "model": get_settings().question_import_s2_model,
                       "prompt_version": ANSWER_GENERATION_VERSION, "response_id": response_id,
                       "input_fingerprint": entry["fingerprint"],
                       "generated_at": datetime.now(timezone.utc).isoformat()}
            payload["answers"] = [*(payload.get("answers") or []), version]
            payload["selected_answer_id"] = version["answer_id"]
            payload["precheck_status"] = "not_run"
            payload["teacher_decision"] = "pending"
            warnings = [value for value in payload.get("warnings") or []
                        if not value.startswith("模型无法确定答案：")]
            if "模型生成答案须由教师核对" not in warnings:
                warnings.append("模型生成答案须由教师核对")
            payload["warnings"] = warnings
            applied += 1
        else:
            warnings = [value for value in payload.get("warnings") or []
                        if not value.startswith("模型无法确定答案：")]
            warnings.append("模型无法确定答案：" + result["uncertainty"].strip()[:500])
            payload["warnings"] = warnings
        item.revision += 1
        payload["revision"] = item.revision
        if payload.get("label_suggestion"):
            payload["label_suggestion"] = {**payload["label_suggestion"], "revision": item.revision}
        item.payload = payload
    return applied, skipped


def process_answer_generation_one() -> bool:
    with SessionLocal() as db:
        rows = list(db.scalars(select(QuestionImportBatch).where(
            QuestionImportBatch.owner_scope == OWNER_SCOPE,
            QuestionImportBatch.status == "review_pending",
            QuestionImportBatch.stage == "s2_completed").order_by(QuestionImportBatch.created_at)
            .with_for_update(skip_locked=True)))
        now = datetime.now(timezone.utc)
        timeout = timedelta(seconds=get_settings().question_import_s2_timeout_seconds + 120)

        def runnable(row: QuestionImportBatch) -> bool:
            state = ((row.checkpoint or {}).get("s3") or {}).get("answer_generation") or {}
            if state.get("status") == "queued":
                return True
            if state.get("status") != "processing":
                return False
            try:
                return datetime.fromisoformat(state["started_at"]) <= now - timeout
            except (KeyError, TypeError, ValueError):
                return True

        batch = next((row for row in rows if runnable(row)), None)
        if batch is None:
            return False
        batch_id = batch.id
        checkpoint = dict(batch.checkpoint or {})
        s3 = dict(checkpoint.get("s3") or {})
        state = deepcopy(s3["answer_generation"])
        if state["status"] == "processing":
            state.update({"status": "paused", "cost_unknown": True,
                          "error_code": "S3_ANSWER_COST_UNKNOWN",
                          "error_message": "答案生成调用中断，用量未确认；请先核对账单"})
            s3["answer_generation"] = state
            checkpoint["s3"] = s3
            batch.checkpoint = checkpoint
            db.commit()
            return True
        group = next((row for row in state["groups"] if row["status"] == "pending"), None)
        if group is None:
            state["status"] = "completed"
            s3["answer_generation"] = state
            checkpoint["s3"] = s3
            batch.checkpoint = checkpoint
            db.commit()
            return True
        current = {str(item.id): item for item in db.scalars(select(QuestionImportItem).where(
            QuestionImportItem.batch_id == batch_id).with_for_update())}
        active = [entry for entry in group["items"]
                  if (item := current.get(entry["item_id"])) is not None and _eligible(item)
                  and item.revision == entry["revision"] and _fingerprint(item) == entry["fingerprint"]]
        skipped = [entry["item_id"] for entry in group["items"] if entry not in active]
        if not active:
            group.update({"status": "completed", "applied_count": 0, "skipped_item_ids": skipped})
            state["status"] = "completed" if not any(row["status"] == "pending" for row in state["groups"]) else "queued"
            s3["answer_generation"] = state
            checkpoint["s3"] = s3
            batch.checkpoint = checkpoint
            db.commit()
            return True
        if float(state["estimated_cny"]) >= float(state["budget_cny"]):
            state.update({"status": "paused", "error_code": "S3_ANSWER_BUDGET",
                          "error_message": "答案生成预算已用完，请增加预算后继续"})
            s3["answer_generation"] = state
            checkpoint["s3"] = s3
            batch.checkpoint = checkpoint
            db.commit()
            return True
        state.update({"status": "processing", "started_at": now.isoformat()})
        s3["answer_generation"] = state
        checkpoint["s3"] = s3
        batch.checkpoint = checkpoint
        db.commit()
        group = {**group, "items": active, "skipped_item_ids": skipped}
        failure: ImportProblem | None = None
        if batch.files_purged_at or batch.expires_at <= now:
            failure = ImportProblem("FILE_EXPIRED", "原文件和题图已过期，请重新上传", 409)
        try:
            images = {entry["item_id"]: [(asset_id, image_bytes(db, batch_id, uuid.UUID(asset_id)))
                                         for asset_id in entry["asset_ids"]]
                      for entry in active}
        except (ImportProblem, ValueError):
            images = {}
            failure = ImportProblem("S3_ANSWER_IMAGE_MISSING", "候选题图片不可用，请核对题图", 409)
    provider = QwenAnswerGenerationProvider()
    try:
        if failure:
            raise failure
        answers, meta = provider.generate(group["items"], images)
        answers = validate_answers({"answers": answers}, group["items"])
    except Exception as exc:
        if not isinstance(exc, ImportProblem):
            logger.exception("S3 answer generation failed for batch %s", batch_id)
            exc = ImportProblem("S3_ANSWER_WORKER_FAILED", "答案生成任务失败；请检查服务端日志")
        raw = provider.last_response
        usage = (raw.get("usage") or {}) if isinstance(raw, dict) else {}
        known_cost = _text_cost(usage) if usage else None
        with SessionLocal() as db:
            batch = db.scalar(select(QuestionImportBatch).where(
                QuestionImportBatch.id == batch_id).with_for_update())
            if batch is not None:
                checkpoint = dict(batch.checkpoint or {})
                s3 = dict(checkpoint.get("s3") or {})
                state = deepcopy(s3["answer_generation"])
                unit = next(row for row in state["groups"] if row["index"] == group["index"])
                state["calls"] += 0 if failure else 1
                attempt = {"at": datetime.now(timezone.utc).isoformat(), "error_code": exc.code,
                           "response_id": raw.get("id") if isinstance(raw, dict) else None,
                           "usage": usage, "estimated_cny": known_cost}
                if known_cost is not None:
                    state["estimated_cny"] = round(float(state["estimated_cny"]) + known_cost, 6)
                    unit["usage"] = usage
                    unit["response_id"] = raw.get("id")
                if isinstance(raw, dict):
                    raw_text = json.dumps(raw, ensure_ascii=False)
                    unit["raw_response_excerpt"] = raw_text[:64_000]
                    unit["raw_response_truncated"] = len(raw_text) > 64_000
                    attempt["raw_response_excerpt"] = raw_text[:64_000]
                    attempt["raw_response_truncated"] = len(raw_text) > 64_000
                if not failure:
                    unit.setdefault("attempts", []).append(attempt)
                state.update({"status": "failed", "error_code": exc.code, "error_message": str(exc),
                              "cost_unknown": not bool(failure) and known_cost is None})
                s3["answer_generation"] = state
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
        state = deepcopy(s3["answer_generation"])
        unit = next(row for row in state["groups"] if row["index"] == group["index"])
        if unit["status"] == "completed":
            return True
        applied, late_skipped = _apply_answers(db, batch, group, answers, response_id=meta["response_id"])
        unit.update({"status": "completed", "answers": answers, "applied_count": applied,
                     "skipped_item_ids": [*group["skipped_item_ids"], *late_skipped], **meta})
        unit.setdefault("attempts", []).append({
            "at": datetime.now(timezone.utc).isoformat(), "response_id": meta["response_id"],
            "usage": meta["usage"], "estimated_cny": meta["estimated_cny"]})
        state["calls"] += 1
        if meta["estimated_cny"] is None:
            state.update({"status": "paused", "cost_unknown": True,
                          "error_code": "S3_ANSWER_COST_UNKNOWN",
                          "error_message": "本组用量未知，请核对账单"})
        else:
            state["estimated_cny"] = round(float(state["estimated_cny"]) + meta["estimated_cny"], 6)
            state["status"] = "completed" if not any(row["status"] == "pending" for row in state["groups"]) else "queued"
        s3["answer_generation"] = state
        checkpoint["s3"] = s3
        batch.checkpoint = checkpoint
        db.commit()
    return True
