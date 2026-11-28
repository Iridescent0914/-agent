"""Explicit, resumable extraction of a separate source-answer document."""

from __future__ import annotations

import base64
import json
import logging
import os
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.models import QuestionImportBatch
from app.services.question_import import (
    DOCX_CONVERSION_VERSION, ImportProblem, OWNER_SCOPE, _path_for, require_s2_operator_token,
)
from app.services.question_import_s2 import (
    FLASH_MODEL, ModelSourceAnswer, estimate_cost, pdf_unit_bytes, plan_units, prepare_pdf,
)
from app.services.question_import_s3 import reconcile_source_answers
from app.services.question_import_s3_local import plan_answer_units

logger = logging.getLogger(__name__)
SEPARATE_ANSWER_VERSION = "s3-separate-answers-v3"


class UnresolvedAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    page: int = Field(ge=1)
    text: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class AnswerPageOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    complete: bool
    source_answers: list[ModelSourceAnswer]
    unresolved_fragments: list[UnresolvedAnswer] = Field(default_factory=list)
    incomplete_from_page: int | None = Field(default=None, ge=1)


def validate_answer_output(value: object, page_count: int) -> AnswerPageOutput:
    try:
        output = AnswerPageOutput.model_validate(value)
    except ValidationError as exc:
        raise ImportProblem("INVALID_MODEL_OUTPUT", "答案文件模型输出不符合字段契约") from exc
    if (any(answer.page_start > answer.page_end or answer.page_end > page_count
            for answer in output.source_answers)
            or any(fragment.page > page_count for fragment in output.unresolved_fragments)):
        raise ImportProblem("INVALID_MODEL_PAGE", "答案文件模型输出页码越界")
    if not output.complete:
        raise ImportProblem("INCOMPLETE_MODEL_OUTPUT", "答案文件页段未完整转录，请检查原页后重试")
    return output


def decode_answer_response(body: dict, *, page_count: int, elapsed: float) -> tuple[AnswerPageOutput, dict]:
    try:
        choice = body["choices"][0]
        finish_reason = choice["finish_reason"]
        value = json.loads(choice["message"]["content"])
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise ImportProblem("INVALID_MODEL_OUTPUT", "答案文件模型未返回有效 JSON") from exc
    if finish_reason != "stop":
        raise ImportProblem("MODEL_OUTPUT_TRUNCATED", "答案文件模型输出被截断")
    output = validate_answer_output(value, page_count)
    return output, {"usage": body.get("usage") or {}, "response_id": body.get("id"),
                    "elapsed_seconds": round(elapsed, 3), "prompt_version": SEPARATE_ANSWER_VERSION}


class QwenSeparateAnswerProvider:
    def __init__(self, *, transport: httpx.BaseTransport | None = None):
        self.settings = get_settings()
        self.transport = transport

    def extract(self, pdf_data: bytes, *, page_count: int, core_page_count: int,
                capture_response=None) -> tuple[AnswerPageOutput, dict]:
        prompt = (
            "你是数学资料答案转录员。此 PDF 仅是答案文件，不要提取题目或创建题目。"
            f"共 {page_count} 页，前 {core_page_count} 页为主体页，后续页仅用于补全主体页开始的答案。"
            "只输出主体页开始的原文标准答案；保留原题号、小题号、答案原文、起止页码。"
            "题干摘录仅在原文确实出现时填写 question_excerpt，不要推测或改写。"
            "保留原文数学公式。不要把解题步骤、解析、题目重述或目录写进 answer_text；"
            "如果标准答案与解析相邻，只摘录明确的最终答案。无法判断答案边界、题号或结果时，"
            "保留可辨内容并在 uncertain_fragments 或 unresolved_fragments 说明，不要猜测。"
            "同一题的小题分别输出；同一结果重复出现只输出一次。"
            "若标准答案只有图形、无法可靠转成文字，不要编造文字答案；"
            "在 unresolved_fragments 记录该题题号、页码和无法转录的原因，留给教师核对。"
            "此类已记录的图形答案不算页段未完成。"
            "页码使用本 PDF 内从 1 开始的页码。complete=true 表示主体页开始的每个标准答案"
            "都已转录到 source_answers，或因确实无法辨认而明确记录在 unresolved_fragments；"
            "仅当主体页开始的答案延续到本 PDF 之外，或仍有未处理的主体页答案时才设为 false。"
            "若 complete=false，填写 incomplete_from_page：从该页开始尚未完整转录；"
            "其之前所有主体页开始的答案必须已完整列出。无法确定起点时填 null。"
            "若 complete=true，incomplete_from_page 填 null。"
            "后续上下文页新开始的题目不影响 complete。输出 complete、source_answers、unresolved_fragments。"
        )
        payload = {"model": self.settings.question_import_s2_model,
                   "messages": [{"role": "user", "content": [
                       {"type": "file", "file": {"file_data": "data:application/pdf;base64," + base64.b64encode(pdf_data).decode("ascii"),
                                                  "filename": "answer-pages.pdf"}},
                       {"type": "text", "text": prompt},
                   ]}],
                   "response_format": {"type": "json_schema", "json_schema": {
                       "name": "separate_source_answers", "strict": True,
                       "schema": AnswerPageOutput.model_json_schema()}},
                   "include_tool_usage": True,
                   "max_tokens": self.settings.question_import_s2_max_output_tokens}
        start = time.monotonic()
        try:
            with httpx.Client(timeout=self.settings.question_import_s2_timeout_seconds, transport=self.transport) as client:
                response = client.post(self.settings.question_import_s2_base_url.rstrip("/") + "/chat/completions",
                                       headers={"Authorization": "Bearer " + self.settings.question_import_s2_api_key},
                                       json=payload)
                response.raise_for_status()
                body = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise ImportProblem("MODEL_REQUEST_FAILED", "答案文件模型请求失败；请核对百炼账单") from exc
        elapsed = time.monotonic() - start
        if capture_response is not None:
            capture_response(body, elapsed)
        return decode_answer_response(body, page_count=page_count, elapsed=elapsed)


def _answer_file(batch: QuestionImportBatch) -> dict:
    return next(file for file in batch.files if file["role"] == "answers")


def _artifact_path(batch_id: uuid.UUID, file_id: str, index: int) -> Path:
    source = _path_for(batch_id, uuid.UUID(file_id))
    return source.with_name(source.name + f".s3-answer-unit{index}.json")


def _answer_records(output: AnswerPageOutput, source_file_id: str, first_page: int,
                    core_page_count: int) -> tuple[list[dict], list[dict]]:
    answers = [{"source_file_id": source_file_id,
                "original_number": answer.original_number,
                "subquestion_number": answer.subquestion_number,
                "text": answer.answer_text,
                "page_start": first_page + answer.page_start - 1,
                "page_end": first_page + answer.page_end - 1,
                "uncertain_fragments": answer.uncertain_fragments,
                "question_excerpt": answer.question_excerpt,
                "answer_form": answer.answer_form}
               for answer in output.source_answers if answer.page_start <= core_page_count]
    unresolved = [{"page": first_page + row.page - 1, "text": row.text,
                   "reason": row.reason}
                  for row in output.unresolved_fragments if row.page <= core_page_count]
    return answers, unresolved


def _recover_answer_prefix(state: dict, unit: dict, artifact: Path) -> bool:
    """Keep an explicitly complete prefix and retry only the reported suffix."""
    if state.get("error_code") != "INCOMPLETE_MODEL_OUTPUT" or not artifact.is_file():
        return False
    try:
        body = json.loads(artifact.read_text(encoding="utf-8"))["raw_response"]
        choice = body["choices"][0]
        if choice["finish_reason"] != "stop":
            return False
        output = AnswerPageOutput.model_validate(json.loads(choice["message"]["content"]))
    except (OSError, KeyError, IndexError, TypeError, ValueError, ValidationError):
        return False
    core_count = unit["core_last_page"] - unit["first_page"] + 1
    frontier = output.incomplete_from_page
    if output.complete or frontier is None or not 1 < frontier <= core_count:
        return False
    if (any(answer.page_start > answer.page_end or answer.page_end > unit["last_page"] - unit["first_page"] + 1
            for answer in output.source_answers)
            or any(row.page > unit["last_page"] - unit["first_page"] + 1
                   for row in output.unresolved_fragments)):
        return False
    frontier = min([frontier] + [answer.page_start for answer in output.source_answers
                                 if answer.page_start < frontier <= answer.page_end])
    if frontier <= 1:
        return False
    prefix_end = unit["first_page"] + frontier - 2
    prefix_output = AnswerPageOutput(complete=True,
        source_answers=[answer for answer in output.source_answers if answer.page_end < frontier],
        unresolved_fragments=[row for row in output.unresolved_fragments if row.page < frontier])
    answers, unresolved = _answer_records(prefix_output, state["source_file_id"],
                                           unit["first_page"], frontier - 1)
    index = unit["index"]
    prefix = {**unit, "last_page": prefix_end, "core_last_page": prefix_end,
              "status": "completed", "source_answers": answers,
              "unresolved_fragments": unresolved, "recovered_from_response_id": body.get("id")}
    suffix = {**unit, "index": index + 1, "first_page": prefix_end + 1,
              "core_first_page": prefix_end + 1, "status": "pending"}
    state["units"][index:index + 1] = [prefix, suffix]
    for position in range(index + 2, len(state["units"])):
        state["units"][position]["index"] = position
    state.setdefault("recovery_history", []).append({
        "failed_index": index, "first_page": suffix["first_page"],
        "core_last_page": suffix["core_last_page"], "action": "resume_from_page"})
    return True


def _adapt_incomplete_answer_unit(state: dict, batch_id: uuid.UUID, file_id: str) -> None:
    if state.get("error_code") not in {"INCOMPLETE_MODEL_OUTPUT", "MODEL_OUTPUT_TRUNCATED"}:
        return
    index = state.get("failed_unit_index")
    units = state.get("units") or []
    if not isinstance(index, int) or not 0 <= index < len(units):
        raise ImportProblem("S3_CONFIG_CHANGED", "失败的答案页段检查点无效，请联系管理员")
    unit = units[index]
    if (unit["index"] != index or unit["status"] != "pending"
            or any(row["status"] != "pending" for row in units[index + 1:])
            or any(_artifact_path(batch_id, file_id, row["index"]).exists()
                   for row in units[index + 1:])):
        raise ImportProblem("S3_CONFIG_CHANGED", "答案页段状态不一致，请联系管理员核对")

    if _recover_answer_prefix(state, unit, _artifact_path(batch_id, file_id, index)):
        return
    page_count = int(state["page_count"])
    core_count = unit["core_last_page"] - unit["core_first_page"] + 1
    if core_count > 1:
        midpoint = unit["core_first_page"] + (core_count - 1) // 2
        context = max(2, unit["last_page"] - unit["core_last_page"])
        first = {**unit, "core_last_page": midpoint,
                 "last_page": min(page_count, midpoint + context)}
        second = {**unit, "index": index + 1,
                  "first_page": midpoint + 1, "core_first_page": midpoint + 1,
                  "last_page": min(page_count, unit["core_last_page"] + context)}
        units[index:index + 1] = [first, second]
        for position in range(index + 2, len(units)):
            units[position]["index"] = position
        action = "split_core"
    elif state["error_code"] == "MODEL_OUTPUT_TRUNCATED":
        raise ImportProblem("S3_ANSWER_REVIEW_REQUIRED",
                            f"答案文件第 {unit['first_page']} 页输出过长；请拆分原文件或联系管理员调整回答上限")
    elif unit["last_page"] < page_count:
        unit["last_page"] = min(page_count, unit["last_page"] + 2)
        action = "extend_context"
    else:
        raise ImportProblem("S3_ANSWER_REVIEW_REQUIRED",
                            f"答案文件第 {unit['first_page']} 页起始的答案已检查后续全部页面，模型仍未完整转录；请核对原页")
    state.setdefault("recovery_history", []).append({
        "failed_index": index, "first_page": unit["first_page"],
        "core_last_page": unit["core_last_page"], "action": action,
    })


def start_separate_answers(db: Session, batch_id: uuid.UUID, *, budget_cny: float,
                           operator_token: str | None = None,
                           automatic: bool = False) -> QuestionImportBatch:
    settings = get_settings()
    if not automatic:
        require_s2_operator_token(operator_token)
    if (not settings.question_import_s2_enabled or not settings.question_import_s2_api_key
            or not settings.question_import_s2_base_url.startswith("https://")
            or settings.question_import_s2_model != FLASH_MODEL):
        raise ImportProblem("S2_NOT_CONFIGURED", "答案文件识别模型尚未配置", 409)
    if not 0 < budget_cny <= settings.question_import_s2_max_budget_cny:
        raise ImportProblem("INVALID_S3_BUDGET", "答案识别预算超出允许范围")
    batch = db.scalar(select(QuestionImportBatch).where(
        QuestionImportBatch.id == batch_id, QuestionImportBatch.owner_scope == OWNER_SCOPE,
    ).with_for_update())
    if batch is None:
        raise ImportProblem("NOT_FOUND", "导入批次不存在", 404)
    if batch.source_mode != "separate" or batch.status != "review_pending" or batch.stage != "s2_completed":
        raise ImportProblem("S3_SOURCE_MODE", "仅已完成题目识别的分文件批次可以识别答案", 409)
    if (((batch.checkpoint or {}).get("s2") or {}).get("auto_global_dedupe")
            and (((batch.checkpoint or {}).get("s3") or {}).get("global_dedupe") or {}).get("status") != "completed"):
        raise ImportProblem("S3_DEDUPE_PENDING", "请先完成整批候选题去重，再识别答案文件", 409)
    if batch.files_purged_at or batch.expires_at <= datetime.now(timezone.utc):
        raise ImportProblem("FILE_EXPIRED", "答案文件已过期，请重新上传", 409)
    checkpoint = dict(batch.checkpoint or {})
    s3 = dict(checkpoint.get("s3") or {})
    state = dict(s3.get("separate_answers") or {})
    if state.get("status") in {"queued", "processing", "completed"}:
        raise ImportProblem("COMMIT_CONFLICT", "答案识别已排队、进行中或完成", 409)
    if budget_cny <= float(state.get("estimated_cny") or 0):
        raise ImportProblem("INVALID_S3_BUDGET", "新预算须高于已经记录的估算费用")
    if state.get("cost_unknown"):
        raise ImportProblem("S3_ANSWER_COST_UNKNOWN", "上次调用用量未知，请核对账单后重新上传", 409)
    source = _answer_file(batch)
    if (source["media_type"] != "application/pdf" and state.get("units")
            and state.get("conversion_version") != DOCX_CONVERSION_VERSION):
        raise ImportProblem("S3_CONFIG_CHANGED", "答案 DOCX 转换器版本变化，请重新上传", 409)
    _adapt_incomplete_answer_unit(state, batch_id, source["file_id"])
    state.update({"status": "queued", "version": SEPARATE_ANSWER_VERSION,
                  "budget_cny": budget_cny, "source_file_id": source["file_id"],
                  "auto_answer_matching": True,
                  "source_sha256": source["sha256"], "model": settings.question_import_s2_model,
                  "price_snapshot": {"pdf_page_cny": settings.question_import_s2_pdf_page_price_cny,
                                     "input_million_cny": settings.question_import_s2_input_million_price_cny,
                                     "output_million_cny": settings.question_import_s2_output_million_price_cny},
                  "conversion_version": DOCX_CONVERSION_VERSION if source["media_type"] != "application/pdf" else None})
    state.setdefault("units", [])
    state.setdefault("calls", 0)
    state.setdefault("estimated_cny", 0.0)
    state.pop("error_code", None)
    state.pop("error_message", None)
    failed_index = state.pop("failed_unit_index", None)
    if failed_index is not None:
        artifact = _artifact_path(batch_id, source["file_id"], failed_index)
        if artifact.is_file():
            try:
                reusable = "output" in json.loads(artifact.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                reusable = False
            if not reusable:
                prior = artifact.with_name(artifact.name + ".prior.json")
                suffix = 2
                while prior.exists():
                    prior = artifact.with_name(artifact.name + f".prior{suffix}.json")
                    suffix += 1
                os.replace(artifact, prior)
    s3["separate_answers"] = state
    checkpoint["s3"] = s3
    batch.checkpoint = checkpoint
    return batch


def _save_artifact(path: Path, value: dict) -> None:
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    os.replace(temp, path)


def _claim(db: Session) -> tuple[uuid.UUID, str] | None:
    current = datetime.now(timezone.utc)
    rows = db.scalars(select(QuestionImportBatch).where(
        QuestionImportBatch.owner_scope == OWNER_SCOPE,
        QuestionImportBatch.status == "review_pending",
        QuestionImportBatch.stage == "s2_completed",
        QuestionImportBatch.files_purged_at.is_(None),
        QuestionImportBatch.expires_at > current,
    ).order_by(QuestionImportBatch.created_at).with_for_update(skip_locked=True))
    for batch in rows:
        checkpoint = dict(batch.checkpoint or {})
        s3 = dict(checkpoint.get("s3") or {})
        state = dict(s3.get("separate_answers") or {})
        if state.get("status") != "queued" and not (
            state.get("status") == "processing" and state.get("lease_until")
            and datetime.fromisoformat(state["lease_until"]) < current
        ):
            continue
        token = str(uuid.uuid4())
        state.update({"status": "processing", "worker_token": token,
                      "lease_until": (current + timedelta(seconds=max(600, int(get_settings().question_import_s2_timeout_seconds) + 120))).isoformat()})
        s3["separate_answers"] = state
        checkpoint["s3"] = s3
        batch.checkpoint = checkpoint
        return batch.id, token
    return None


def _update(db: Session, batch_id: uuid.UUID, token: str, state: dict) -> bool:
    batch = db.scalar(select(QuestionImportBatch).where(
        QuestionImportBatch.id == batch_id, QuestionImportBatch.owner_scope == OWNER_SCOPE,
    ).with_for_update())
    if batch is None:
        return False
    checkpoint = dict(batch.checkpoint or {})
    s3 = dict(checkpoint.get("s3") or {})
    current = s3.get("separate_answers") or {}
    if current.get("worker_token") != token:
        return False
    s3["separate_answers"] = state
    checkpoint["s3"] = s3
    batch.checkpoint = checkpoint
    return True


def process_separate_answers_one() -> bool:
    with SessionLocal() as db:
        claimed = _claim(db)
        db.commit()
    if claimed is None:
        return False
    batch_id, token = claimed
    current_index = None
    try:
        with SessionLocal() as db:
            batch = db.get(QuestionImportBatch, batch_id)
            source = _answer_file(batch)
            state = dict(((batch.checkpoint or {}).get("s3") or {})["separate_answers"])
        if state["source_sha256"] != source["sha256"]:
            raise ImportProblem("SOURCE_CHANGED", "答案文件已变化")
        settings = get_settings()
        prices = {"pdf_page_cny": settings.question_import_s2_pdf_page_price_cny,
                  "input_million_cny": settings.question_import_s2_input_million_price_cny,
                  "output_million_cny": settings.question_import_s2_output_million_price_cny}
        if state["model"] != settings.question_import_s2_model or state["price_snapshot"] != prices:
            raise ImportProblem("S3_CONFIG_CHANGED", "答案识别模型或价格配置已改变")
        pdf, pages, warnings = prepare_pdf(batch_id, source)
        if state.get("page_count") and state["page_count"] != pages:
            raise ImportProblem("SOURCE_CHANGED", "答案文件转换后页数已变化")
        if not state["units"]:
            state["page_count"] = pages
            state["conversion_warnings"] = warnings
            try:
                state["units"] = plan_answer_units(
                    pdf, pages_per_unit=settings.question_import_s2_pages_per_unit,
                    overlap=settings.question_import_s2_overlap_pages)
            except (OSError, RuntimeError, ValueError):
                logger.exception("Local answer page planning failed for batch %s; using PDF units", batch_id)
                state["units"] = [{**unit.__dict__, "status": "pending"} for unit in plan_units(
                    pages, pages_per_unit=settings.question_import_s2_pages_per_unit,
                    overlap=settings.question_import_s2_overlap_pages)]
            with SessionLocal() as db:
                if not _update(db, batch_id, token, state):
                    return True
                db.commit()
        unit_data = next((unit for unit in state["units"] if unit["status"] != "completed"), None)
        if unit_data is None:
            state["status"] = "completed"
            state.pop("worker_token", None)
            state.pop("lease_until", None)
            with SessionLocal() as db:
                if _update(db, batch_id, token, state):
                    reconcile_source_answers(db, batch_id, force=True)
                    db.commit()
            return True
        current_index = unit_data["index"]
        is_local = unit_data.get("method") == "local_text"
        if not is_local and (state.get("cost_unknown") or float(state["estimated_cny"]) >= float(state["budget_cny"])):
            raise ImportProblem("S3_ANSWER_BUDGET", "答案识别预算已达到或用量未知，请核对账单并提高预算")
        if not is_local and int(state["calls"]) >= settings.question_import_s2_max_calls:
            raise ImportProblem("S3_ANSWER_CALL_LIMIT", "答案识别已达到单批调用次数上限")
        from app.services.question_import_s2 import PageUnit
        unit = PageUnit(**{field: unit_data[field] for field in PageUnit.__dataclass_fields__})
        artifact = _artifact_path(batch_id, source["file_id"], unit.index)
        if is_local:
            output = AnswerPageOutput(complete=True, source_answers=unit_data["local_answers"])
            meta = {"usage": {}, "response_id": None, "elapsed_seconds": 0.0}
        elif artifact.is_file():
            saved = json.loads(artifact.read_text(encoding="utf-8"))
            if "output" in saved:
                output = validate_answer_output(saved["output"], unit.page_count)
                meta = saved["meta"]
            else:
                output, meta = decode_answer_response(saved["raw_response"], page_count=unit.page_count,
                                                      elapsed=saved["elapsed_seconds"])
        else:
            provider = QwenSeparateAnswerProvider()
            output, meta = provider.extract(
                pdf_unit_bytes(pdf, unit), page_count=unit.page_count,
                core_page_count=unit.core_last_page - unit.first_page + 1,
                capture_response=lambda body, elapsed: _save_artifact(
                    artifact, {"raw_response": body, "elapsed_seconds": elapsed}),
            )
            _save_artifact(artifact, {"output": output.model_dump(mode="json"), "meta": meta})
        cost = 0.0 if is_local else estimate_cost(meta.get("usage") or {})
        answers, unresolved = _answer_records(output, source["file_id"], unit.first_page,
                                              unit.core_last_page - unit.first_page + 1)
        unit_data.update({"status": "completed", "source_answers": answers,
                          "unresolved_fragments": unresolved, "usage": meta.get("usage") or {},
                          "response_id": meta.get("response_id"), "estimated_cny": cost,
                          "elapsed_seconds": meta.get("elapsed_seconds")})
        if not is_local:
            state["calls"] = int(state["calls"]) + 1
        if cost is None:
            state["cost_unknown"] = True
        else:
            state["estimated_cny"] = round(float(state["estimated_cny"]) + cost, 6)
        if all(row["status"] == "completed" for row in state["units"]):
            state["status"] = "completed"
        else:
            state["status"] = "queued"
        state.pop("worker_token", None)
        state.pop("lease_until", None)
        with SessionLocal() as db:
            if _update(db, batch_id, token, state):
                if state["status"] == "completed":
                    reconcile_source_answers(db, batch_id, force=True)
                db.commit()
    except Exception as exc:
        logger.exception("Separate answer extraction failed for batch %s", batch_id)
        with SessionLocal() as db:
            batch = db.get(QuestionImportBatch, batch_id)
            if batch is not None:
                checkpoint = dict(batch.checkpoint or {})
                s3 = dict(checkpoint.get("s3") or {})
                state = dict(s3.get("separate_answers") or {})
                if state.get("worker_token") == token:
                    if current_index is not None:
                        artifact = _artifact_path(batch_id, state["source_file_id"], current_index)
                        try:
                            saved = json.loads(artifact.read_text(encoding="utf-8"))
                            if "raw_response" in saved:
                                usage = saved["raw_response"].get("usage") or {}
                                cost = estimate_cost(usage)
                                state["calls"] = int(state.get("calls") or 0) + 1
                                if cost is None:
                                    state["cost_unknown"] = True
                                else:
                                    state["estimated_cny"] = round(float(state.get("estimated_cny") or 0) + cost, 6)
                        except (OSError, KeyError, TypeError, ValueError):
                            pass
                    state["status"] = "paused" if isinstance(exc, ImportProblem) and exc.code in {
                        "S3_ANSWER_BUDGET", "S3_ANSWER_CALL_LIMIT"} else "failed"
                    state["error_code"] = exc.code if isinstance(exc, ImportProblem) else "S3_ANSWER_FAILED"
                    state["error_message"] = str(exc) if isinstance(exc, ImportProblem) else "答案识别失败；请检查日志和账单"
                    state["failed_unit_index"] = current_index
                    state.pop("worker_token", None)
                    state.pop("lease_until", None)
                    s3["separate_answers"] = state
                    checkpoint["s3"] = s3
                    batch.checkpoint = checkpoint
                    db.commit()
    return True
