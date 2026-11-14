"""Explicitly started, resumable S2 extraction worker."""

from __future__ import annotations

import json
import hashlib
import os
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.models import QuestionImportBatch, QuestionImportItem
from app.services.question_import import DOCX_CONVERSION_VERSION, OWNER_SCOPE, ImportProblem, _path_for, add_candidate, get_batch, require_s2_operator_token
from app.services.question_import_s2 import (
    FLASH_MODEL, LEGACY_PROMPT_VERSION, PROMPT_VERSION, ModelOutput, PageUnit, QwenPdfProvider,
    candidate_from_question, candidate_key,
    decode_qwen_response, estimate_cost, overlap_candidate_is_duplicate, prefer_incoming_overlap,
    pdf_unit_bytes, plan_units, prepare_pdf,
    resolved_overlap_option,
)


def start_s2(db: Session, batch_id: uuid.UUID, *, budget_cny: float, operator_token: str | None = None) -> QuestionImportBatch:
    settings = get_settings()
    if settings.question_import_s2_model != FLASH_MODEL:
        raise ImportProblem("S2_MODEL_NOT_SUPPORTED", "S2 当前仅支持 Flash 模型", 409)
    if not settings.question_import_s2_enabled or not settings.question_import_s2_api_key or not settings.question_import_s2_base_url.startswith("https://"):
        raise ImportProblem("S2_NOT_CONFIGURED", "S2 百炼调用尚未在服务端配置", 409)
    require_s2_operator_token(operator_token)
    if not 0 < budget_cny <= settings.question_import_s2_max_budget_cny:
        raise ImportProblem("INVALID_S2_BUDGET", "本批预算超出服务端允许范围")
    batch = db.scalar(select(QuestionImportBatch).where(
        QuestionImportBatch.id == batch_id, QuestionImportBatch.owner_scope == OWNER_SCOPE,
    ).with_for_update())
    if batch is None:
        raise ImportProblem("NOT_FOUND", "导入批次不存在", 404)
    if batch.files_purged_at is not None or batch.expires_at <= datetime.now(timezone.utc):
        raise ImportProblem("FILE_EXPIRED", "临时文件已过期，请重新上传", 409)
    if batch.stage not in {"awaiting_s2", "s2_budget_paused"} or batch.status not in {"uploaded", "paused"}:
        raise ImportProblem("COMMIT_CONFLICT", "批次当前不能启动 S2", 409)
    if not any(item["role"] == "questions" for item in batch.files):
        raise ImportProblem("INVALID_FILE", "批次缺少题目文件")
    checkpoint = dict(batch.checkpoint or {})
    s2 = dict(checkpoint.get("s2") or {})
    question_file = next(item for item in batch.files if item["role"] == "questions")
    if (question_file["media_type"] == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            and s2.get("units") and s2.get("conversion_version") != DOCX_CONVERSION_VERSION):
        raise ImportProblem("S2_CONFIG_CHANGED", "DOCX 转换器已修复公式处理，请重新上传以完整重跑 S2", 409)
    if budget_cny <= float(s2.get("estimated_cny") or 0):
        raise ImportProblem("INVALID_S2_BUDGET", "新预算必须高于已估算费用")
    s2["budget_cny"] = budget_cny
    if s2.get("model") and s2["model"] != settings.question_import_s2_model:
        raise ImportProblem("S2_CONFIG_CHANGED", "模型配置已变化，请新建批次", 409)
    s2["model"] = settings.question_import_s2_model
    s2["prompt_version"] = PROMPT_VERSION
    if question_file["media_type"] == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
        s2["conversion_version"] = DOCX_CONVERSION_VERSION
    prices = {
        "pdf_page_cny": settings.question_import_s2_pdf_page_price_cny,
        "input_million_cny": settings.question_import_s2_input_million_price_cny,
        "output_million_cny": settings.question_import_s2_output_million_price_cny,
    }
    if s2.get("price_snapshot") and s2["price_snapshot"] != prices:
        raise ImportProblem("S2_CONFIG_CHANGED", "计费单价配置已变化，请新建批次", 409)
    s2["price_snapshot"] = prices
    s2.setdefault("units", [])
    s2.setdefault("estimated_cny", 0.0)
    s2.setdefault("calls", 0)
    checkpoint["s2"] = s2
    batch.checkpoint = checkpoint
    batch.status = "uploaded"
    batch.stage = "s2_queued"
    batch.error_code = batch.error_message = None
    return batch


def claim_s2(db: Session, *, now: datetime | None = None) -> tuple[uuid.UUID, uuid.UUID] | None:
    settings = get_settings()
    current = now or datetime.now(timezone.utc)
    row = db.scalar(select(QuestionImportBatch).where(
        QuestionImportBatch.owner_scope == OWNER_SCOPE,
        QuestionImportBatch.files_purged_at.is_(None),
        QuestionImportBatch.expires_at > current,
        or_(
            (QuestionImportBatch.status == "uploaded") & (QuestionImportBatch.stage == "s2_queued"),
            (QuestionImportBatch.status == "parsing") & (QuestionImportBatch.stage == "s2_processing")
            & (QuestionImportBatch.lease_until < current),
        ),
    ).order_by(QuestionImportBatch.created_at, QuestionImportBatch.id).with_for_update(skip_locked=True))
    if row is None:
        return None
    if row.stage == "s2_processing" and row.attempt_count >= settings.question_import_max_attempts:
        row.status = "failed"
        row.stage = "s2_failed"
        row.error_code = "WORKER_TIMEOUT"
        row.error_message = "S2 任务多次超时；已完成页段已保留，可重试"
        row.worker_token = None
        row.lease_until = None
        return None
    token = uuid.uuid4()
    row.status = "parsing"
    row.stage = "s2_processing"
    row.worker_token = token
    row.heartbeat_at = current
    row.lease_until = current + timedelta(seconds=max(settings.question_import_lease_seconds,
                                                        int(settings.question_import_s2_timeout_seconds) + 120))
    row.attempt_count += 1
    db.flush()
    return row.id, token


def _locked_batch(db: Session, batch_id: uuid.UUID, token: uuid.UUID) -> QuestionImportBatch | None:
    return db.scalar(select(QuestionImportBatch).where(
        QuestionImportBatch.id == batch_id,
        QuestionImportBatch.worker_token == token,
        QuestionImportBatch.status == "parsing",
        QuestionImportBatch.stage == "s2_processing",
    ).with_for_update())


def _checkpoint_update(db: Session, batch_id: uuid.UUID, token: uuid.UUID, s2: dict) -> bool:
    row = _locked_batch(db, batch_id, token)
    if row is None:
        return False
    checkpoint = dict(row.checkpoint or {})
    checkpoint["s2"] = s2
    row.checkpoint = checkpoint
    now = datetime.now(timezone.utc)
    row.heartbeat_at = now
    row.lease_until = now + timedelta(seconds=max(get_settings().question_import_lease_seconds,
                                                 int(get_settings().question_import_s2_timeout_seconds) + 120))
    db.commit()
    return True


def _result_path(batch_id: uuid.UUID, file_id: str, unit_index: int):
    return _path_for(batch_id, uuid.UUID(file_id)).with_name(file_id.replace("-", "") + f".unit{unit_index}.json")


def _trim_redundant_pending_tail(s2: dict, batch_id: uuid.UUID, file_id: str) -> bool:
    """Drop only legacy unstarted tail units already covered by the preceding unit."""
    if s2.get("recovery_history"):
        return False
    units = s2["units"]
    changed = False
    while len(units) > 1:
        tail, previous = units[-1], units[-2]
        if (tail["core_last_page"] > previous["last_page"]
                or previous["status"] != "completed"
                or tail["status"] != "pending"
                or (s2.get("failed_unit") or {}).get("index") == tail["index"]
                or _result_path(batch_id, file_id, tail["index"]).is_file()):
            break
        units.pop()
        changed = True
    return changed


def _cover_final_page(s2: dict, page_count: int) -> bool:
    """The final overlap has no following unit, so it belongs to the final core."""
    tail = s2["units"][-1]
    if tail["last_page"] != page_count or tail["core_last_page"] >= page_count:
        return False
    if tail["status"] != "pending":
        raise ImportProblem("S2_CONFIG_CHANGED", "尾页已按旧规则处理，请重新建立批次")
    tail["core_last_page"] = page_count
    return True


def _upgrade_incomplete_overlap_prompt(s2: dict) -> bool:
    """Resume a v2 batch whose failed unit ended on a context-only overlap page."""
    if s2.get("prompt_version") != LEGACY_PROMPT_VERSION:
        return False
    failed = s2.get("failed_unit") or {}
    if failed.get("code") != "INCOMPLETE_MODEL_OUTPUT":
        return False
    unit = next((row for row in s2.get("units") or [] if row["index"] == failed.get("index")), None)
    if unit is None or unit["last_page"] <= unit["core_last_page"]:
        return False
    s2["prompt_version"] = PROMPT_VERSION
    s2["prompt_upgrade"] = {"from": LEGACY_PROMPT_VERSION, "at_unit_index": unit["index"],
                            "reason": "incomplete_overlap_page"}
    failed["recovery_applied"] = True
    return True


def _adapt_incomplete_unit(s2: dict, batch_id: uuid.UUID, file_id: str) -> bool:
    """On an explicit retry, reduce the failed core or extend its context."""
    failed = s2.get("failed_unit") or {}
    if failed.get("code") != "INCOMPLETE_MODEL_OUTPUT" or failed.get("recovery_applied"):
        return False
    units = s2["units"]
    index = failed.get("index")
    if not isinstance(index, int) or not 0 <= index < len(units):
        raise ImportProblem("S2_CONFIG_CHANGED", "失败页段检查点无效，请联系管理员")
    unit = units[index]
    if unit["status"] != "pending" or any(row["status"] != "pending" for row in units[index + 1:]):
        raise ImportProblem("S2_CONFIG_CHANGED", "失败页段状态不一致，请联系管理员")
    if any(_result_path(batch_id, file_id, row["index"]).exists() for row in units[index + 1:]):
        raise ImportProblem("S2_CONFIG_CHANGED", "后续页段已有响应，请联系管理员核对")
    page_count = int(s2["page_count"])
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
    elif unit["last_page"] < page_count:
        unit["last_page"] = min(page_count, unit["last_page"] + 2)
        action = "extend_context"
    else:
        raise ImportProblem("S2_REVIEW_REQUIRED", f"第 {unit['first_page']} 页起始的题已检查后续全部页面，模型仍无法完整提取；请检查原页并重新上传清晰文件，或联系管理员核对")
    s2.setdefault("recovery_history", []).append({"failed_index": index,
                                                     "first_page": unit["first_page"],
                                                     "core_last_page": unit["core_last_page"],
                                                     "action": action})
    failed["recovery_applied"] = True
    return True


def _extract_unit(batch_id: uuid.UUID, file_id: str, pdf, unit: PageUnit,
                  *, retry_invalid: bool = False) -> tuple[ModelOutput, dict]:
    artifact = _result_path(batch_id, file_id, unit.index)
    if artifact.is_file():
        data = json.loads(artifact.read_text(encoding="utf-8"))
        if "output" in data:
            return ModelOutput.model_validate(data["output"]), data["meta"]
        try:
            return decode_qwen_response(data["raw_response"], page_count=unit.page_count,
                                        elapsed=data["elapsed_seconds"], model=get_settings().question_import_s2_model)
        except ImportProblem:
            if not retry_invalid:
                raise
            prior = artifact.with_name(artifact.name + ".prior.json")
            suffix = 2
            while prior.exists():
                prior = artifact.with_name(artifact.name + f".prior{suffix}.json")
                suffix += 1
            prior.write_bytes(artifact.read_bytes())
    provider = QwenPdfProvider()

    def save_artifact(data: dict) -> None:
        temp = artifact.with_name(artifact.name + ".tmp")
        temp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        os.replace(temp, artifact)

    def capture_response(body: dict, elapsed: float) -> None:
        save_artifact({"raw_response": body, "elapsed_seconds": elapsed})

    output, meta, _ = provider.extract(pdf_unit_bytes(pdf, unit), page_count=unit.page_count,
                                       core_page_count=unit.core_last_page - unit.first_page + 1,
                                       capture_response=capture_response)
    save_artifact({"output": output.model_dump(mode="json"), "meta": meta})
    return output, meta


def process_s2_one() -> bool:
    with SessionLocal() as db:
        claimed = claim_s2(db)
        db.commit()
    if claimed is None:
        return False
    batch_id, token = claimed
    unit: PageUnit | None = None
    try:
        with SessionLocal() as db:
            batch = get_batch(db, batch_id)
            source = next(item for item in batch.files if item["role"] == "questions")
            s2 = dict((batch.checkpoint or {}).get("s2") or {})
        if (source["media_type"] == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                and s2.get("units") and s2.get("conversion_version") != DOCX_CONVERSION_VERSION):
            raise ImportProblem("S2_CONFIG_CHANGED", "DOCX 转换器已修复公式处理，请重新上传以完整重跑 S2")
        pdf, pages, warnings = prepare_pdf(batch_id, source)
        if not s2.get("units"):
            units = plan_units(pages, pages_per_unit=get_settings().question_import_s2_pages_per_unit,
                               overlap=get_settings().question_import_s2_overlap_pages)
            s2["source_file_id"] = source["file_id"]
            s2["source_sha256"] = source["sha256"]
            s2["page_count"] = pages
            s2["conversion_warnings"] = warnings
            s2["units"] = [{**unit.__dict__, "status": "pending"} for unit in units]
            with SessionLocal() as db:
                if not _checkpoint_update(db, batch_id, token, s2):
                    return True
        else:
            if s2.get("source_sha256") != source["sha256"] or s2.get("page_count") != pages:
                raise ImportProblem("SOURCE_CHANGED", "源文件或转换页数与检查点不一致")
            if s2.get("model") != get_settings().question_import_s2_model:
                raise ImportProblem("S2_CONFIG_CHANGED", "模型配置已变化，请新建批次")
            prices = {"pdf_page_cny": get_settings().question_import_s2_pdf_page_price_cny,
                      "input_million_cny": get_settings().question_import_s2_input_million_price_cny,
                      "output_million_cny": get_settings().question_import_s2_output_million_price_cny}
            if s2.get("price_snapshot") != prices:
                raise ImportProblem("S2_CONFIG_CHANGED", "计费单价配置已变化，请新建批次")
            upgraded = _upgrade_incomplete_overlap_prompt(s2)
            if s2.get("prompt_version") != PROMPT_VERSION:
                raise ImportProblem("S2_CONFIG_CHANGED", "提示词版本已变化，请新建批次")
            trimmed = _trim_redundant_pending_tail(s2, batch_id, source["file_id"])
            covered = _cover_final_page(s2, pages)
            adapted = False if upgraded else _adapt_incomplete_unit(s2, batch_id, source["file_id"])
            if upgraded or trimmed or covered or adapted:
                with SessionLocal() as db:
                    if not _checkpoint_update(db, batch_id, token, s2):
                        return True
            units = [PageUnit(**{key: row[key] for key in PageUnit.__dataclass_fields__}) for row in s2["units"]]
        for unit in units:
            state = s2["units"][unit.index]
            if state["status"] == "completed":
                continue
            if s2.get("cost_unknown") or float(s2.get("estimated_cny") or 0) >= float(s2["budget_cny"]):
                _pause(batch_id, token, s2, "BUDGET_REACHED", "预算已达到或部分用量缺失，请核对账单后调整预算")
                return True
            if int(s2.get("calls") or 0) >= get_settings().question_import_s2_max_calls:
                _pause(batch_id, token, s2, "CALL_LIMIT_REACHED", "S2 单批调用次数达到上限")
                return True
            failed_unit = s2.get("failed_unit") or {}
            output, meta = _extract_unit(batch_id, source["file_id"], pdf, unit,
                                         retry_invalid=failed_unit.get("index") == unit.index
                                         and failed_unit.get("code") in {
                                             "INVALID_MODEL_OUTPUT", "INVALID_MODEL_PAGE",
                                             "INCOMPLETE_MODEL_OUTPUT",
                                             "MODEL_OUTPUT_TRUNCATED",
                                         })
            cost = estimate_cost(meta.get("usage") or {})
            with SessionLocal() as db:
                row = _locked_batch(db, batch_id, token)
                if row is None:
                    return True
                current = dict((row.checkpoint or {}).get("s2") or {})
                if current["units"][unit.index]["status"] == "completed":
                    continue
                existing_items = list(db.scalars(select(QuestionImportItem).where(
                    QuestionImportItem.batch_id == batch_id).with_for_update()))
                previous_payloads = [item.payload for item in existing_items]
                known = {candidate_key(item.payload) for item in existing_items}
                selected = 0
                overlap_duplicates = []
                ignored_overlap_questions = []
                for question in output.questions:
                    if question.page_start > unit.core_last_page - unit.first_page + 1:
                        ignored_overlap_questions.append({"original_number": question.original_number,
                                                          "page_start": unit.first_page + question.page_start - 1})
                        continue
                    payload = candidate_from_question(source["file_id"], question, unit)
                    key = candidate_key(payload)
                    if key in known:
                        overlap_duplicates.append({"original_number": payload["source"]["original_number"],
                                                   "page_start": payload["source"]["page_start"],
                                                   "reason": "exact"})
                        continue
                    duplicate = next((item for item in existing_items if overlap_candidate_is_duplicate(
                        item.payload, payload, boundary_page=unit.first_page)), None)
                    if duplicate is not None:
                        replaced = prefer_incoming_overlap(duplicate.payload, payload)
                        if replaced:
                            original = duplicate.payload
                            duplicate.payload = {**payload,
                                                 "batch_id": original["batch_id"],
                                                 "item_id": original["item_id"],
                                                 "revision": original["revision"],
                                                 "catalog_snapshot_sha256": original["catalog_snapshot_sha256"]}
                        overlap_duplicates.append({"original_number": payload["source"]["original_number"],
                                                   "page_start": payload["source"]["page_start"],
                                                   "kept_item_id": str(duplicate.id), "reason": "overlap_variant",
                                                   "replaced_with_richer_candidate": replaced})
                        continue
                    existing_items.append(add_candidate(db, row, payload))
                    known.add(key)
                    selected += 1
                unresolved = [{"page": unit.first_page + fragment.page - 1,
                               "text": fragment.text, "reason": fragment.reason}
                              for fragment in output.unresolved_fragments]
                resolved_overlap = []
                if unit.index > 0:
                    remaining = []
                    for fragment in unresolved:
                        if resolved_overlap_option(fragment, boundary_page=unit.first_page,
                                                   previous=previous_payloads):
                            resolved_overlap.append(fragment)
                        else:
                            remaining.append(fragment)
                    unresolved = remaining
                if not output.questions and not unresolved:
                    if not resolved_overlap:
                        unresolved = [{"page": unit.first_page, "text": "本页段没有识别出题目",
                                       "reason": "需核对原文件是否为空白、答案页或漏题"}]
                current["units"][unit.index] = {**current["units"][unit.index], "status": "completed",
                                                  "candidate_count": selected,
                                                  "unresolved_fragments": unresolved,
                                                  "resolved_overlap_fragments": resolved_overlap,
                                                  "overlap_duplicates": overlap_duplicates,
                                                  "ignored_overlap_questions": ignored_overlap_questions,
                                                  "usage": meta.get("usage") or {},
                                                  "estimated_cny": cost,
                                                  "elapsed_seconds": meta["elapsed_seconds"],
                                                  "response_id": meta.get("response_id")}
                already_accounted = bool(meta.get("response_id") and meta["response_id"] in
                                         (current.get("accounted_response_keys") or []))
                if not already_accounted:
                    current["calls"] = int(current.get("calls") or 0) + 1
                    if cost is None:
                        current["cost_unknown"] = True
                    else:
                        current["estimated_cny"] = round(float(current.get("estimated_cny") or 0) + cost, 6)
                current.pop("failed_unit", None)
                checkpoint = dict(row.checkpoint or {})
                checkpoint["s2"] = current
                row.checkpoint = checkpoint
                remaining = any(item["status"] != "completed" for item in current["units"])
                now = datetime.now(timezone.utc)
                row.heartbeat_at = now
                row.attempt_count = 0
                if remaining:
                    row.status = "uploaded"
                    row.stage = "s2_queued"
                    row.worker_token = None
                    row.lease_until = None
                db.commit()
                s2 = current
                if remaining:
                    return True
        with SessionLocal() as db:
            row = _locked_batch(db, batch_id, token)
            if row is not None:
                row.status = "review_pending"
                row.stage = "s2_completed"
                row.worker_token = None
                row.lease_until = None
                db.commit()
    except Exception as exc:
        with SessionLocal() as db:
            row = _locked_batch(db, batch_id, token)
            if row is not None:
                if unit is not None:
                    checkpoint = dict(row.checkpoint or {})
                    s2 = dict(checkpoint.get("s2") or {})
                    failed_unit = {"index": unit.index, "first_page": unit.first_page,
                                   "last_page": unit.last_page,
                                   "code": exc.code if isinstance(exc, ImportProblem) else "S2_INTERNAL_ERROR"}
                    artifact = _result_path(batch_id, s2["source_file_id"], unit.index)
                    try:
                        saved = json.loads(artifact.read_text(encoding="utf-8"))
                        raw_response = saved.get("raw_response")
                        if isinstance(raw_response, dict):
                            usage = raw_response.get("usage") or {}
                            cost = estimate_cost(usage)
                            response_key = str(raw_response.get("id") or hashlib.sha256(
                                json.dumps(saved, ensure_ascii=False, sort_keys=True).encode("utf-8")
                            ).hexdigest())
                            accounted = list(s2.get("accounted_response_keys") or [])
                            if response_key not in accounted:
                                accounted.append(response_key)
                                s2["accounted_response_keys"] = accounted
                                s2["calls"] = int(s2.get("calls") or 0) + 1
                                if cost is None:
                                    s2["cost_unknown"] = True
                                else:
                                    s2["estimated_cny"] = round(float(s2.get("estimated_cny") or 0) + cost, 6)
                            failed_unit.update({"response_id": raw_response.get("id"),
                                                "usage": usage, "estimated_cny": cost})
                    except (OSError, ValueError, TypeError, KeyError):
                        pass
                    s2["failed_unit"] = failed_unit
                    checkpoint["s2"] = s2
                    row.checkpoint = checkpoint
                row.status = "failed"
                row.stage = "s2_failed"
                row.error_code = exc.code if isinstance(exc, ImportProblem) else "S2_INTERNAL_ERROR"
                message = str(exc) if isinstance(exc, ImportProblem) else "S2 处理失败；请检查服务日志"
                suffix = "" if row.error_code == "S2_REVIEW_REQUIRED" else "。失败调用可能已产生费用，请核对百炼账单"
                row.error_message = (f"第 {unit.first_page}–{unit.last_page} 页：" if unit is not None else "") + message[:180] + suffix
                row.worker_token = None
                row.lease_until = None
                db.commit()
    return True


def _pause(batch_id: uuid.UUID, token: uuid.UUID, s2: dict, code: str, message: str) -> None:
    with SessionLocal() as db:
        row = _locked_batch(db, batch_id, token)
        if row is not None:
            row.status = "paused"
            row.stage = "s2_budget_paused"
            row.error_code = code
            row.error_message = message
            row.worker_token = None
            row.lease_until = None
            db.commit()

