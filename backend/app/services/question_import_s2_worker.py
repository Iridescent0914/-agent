"""Explicitly started, resumable S2 extraction worker."""

from __future__ import annotations

import json
import hashlib
import copy
import logging
import os
import re
import uuid
from datetime import datetime, timedelta, timezone

import pymupdf as fitz
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.models import QuestionImportBatch, QuestionImportItem
from app.services.question_import import DOCX_CONVERSION_VERSION, OWNER_SCOPE, ImportProblem, _path_for, add_candidate, get_batch, require_s2_operator_token, retry_batch
from app.services.question_import_s2 import (
    FLASH_MODEL, LEGACY_PROMPT_VERSION, PROMPT_VERSION, ModelOutput, PageUnit, QwenPdfProvider,
    COMBINED_PROMPT_VERSION, FOCUSED_COMBINED_PROMPT_VERSION, SECTIONED_COMBINED_PROMPT_VERSION,
    INCLUSIVE_COMBINED_PROMPT_VERSION,
    CLASSIFIED_COMBINED_PROMPT_VERSION,
    COMBINED_PROMPT_VERSIONS, LEGACY_COMBINED_PROMPT_VERSION,
    CombinedModelOutput, combined_output_model,
    candidate_from_question, candidate_key,
    decode_qwen_response, estimate_cost, overlap_candidate_is_duplicate,
    pdf_page_roles, pdf_unit_bytes, plan_units, prepare_pdf,
)
from app.services.question_import_s3_images import _render, extract_images, question_figure_assets

logger = logging.getLogger(__name__)
FIGURE_VERSION = "s2-figure-coverage-v1"


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
    if not s2:
        s2["figure_version"] = FIGURE_VERSION
    question_file = next(item for item in batch.files if item["role"] == "questions")
    if (question_file["media_type"] == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            and s2.get("units") and s2.get("conversion_version") != DOCX_CONVERSION_VERSION):
        raise ImportProblem("S2_CONFIG_CHANGED", "DOCX 转换器已修复公式处理，请重新上传以完整重跑 S2", 409)
    if budget_cny <= float(s2.get("estimated_cny") or 0):
        raise ImportProblem("INVALID_S2_BUDGET", "新预算必须高于已估算费用")
    s2["budget_cny"] = budget_cny
    s2["auto_global_dedupe"] = True
    if s2.get("model") and s2["model"] != settings.question_import_s2_model:
        raise ImportProblem("S2_CONFIG_CHANGED", "模型配置已变化，请新建批次", 409)
    s2["model"] = settings.question_import_s2_model
    # Existing paid v3 batches keep their original output contract on resume.
    s2["prompt_version"] = s2.get("prompt_version") or (
        INCLUSIVE_COMBINED_PROMPT_VERSION if batch.source_mode == "combined" else PROMPT_VERSION
    )
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
    """On an explicit retry, reduce the failed core and keep one context page."""
    failed = s2.get("failed_unit") or {}
    if failed.get("code") not in {"INCOMPLETE_MODEL_OUTPUT", "MODEL_OUTPUT_TRUNCATED"} or failed.get("recovery_applied"):
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
        first = {**unit, "core_last_page": midpoint,
                 "last_page": min(page_count, midpoint + 1)}
        second = {**unit, "index": index + 1,
                  "first_page": midpoint + 1, "core_first_page": midpoint + 1,
                  "last_page": min(page_count, unit["core_last_page"] + 1)}
        units[index:index + 1] = [first, second]
        for position in range(index + 2, len(units)):
            units[position]["index"] = position
        action = "split_core"
    elif failed["code"] == "MODEL_OUTPUT_TRUNCATED":
        raise ImportProblem("S2_REVIEW_REQUIRED", f"第 {unit['first_page']} 页题答输出过长；请拆分原文件或联系管理员调整回答上限")
    else:
        raise ImportProblem("S2_REVIEW_REQUIRED", f"第 {unit['first_page']} 页起始的题在单页主体下仍无法完整提取；请检查原页并重新上传清晰文件，或联系管理员核对")
    s2.setdefault("recovery_history", []).append({"failed_index": index,
                                                     "first_page": unit["first_page"],
                                                     "core_last_page": unit["core_last_page"],
                                                     "action": action})
    failed["recovery_applied"] = True
    return True


def skip_incomplete_s2_page(db: Session, batch_id: uuid.UUID, *, page: int,
                            operator_token: str | None = None) -> QuestionImportBatch:
    """Record a reviewed one-page S2 gap and continue with later pending units."""
    batch = db.scalar(select(QuestionImportBatch).where(
        QuestionImportBatch.id == batch_id, QuestionImportBatch.owner_scope == OWNER_SCOPE,
    ).with_for_update())
    if batch is None:
        raise ImportProblem("NOT_FOUND", "导入批次不存在", 404)
    if (batch.status != "failed" or batch.stage != "s2_failed"
            or batch.error_code not in {"INCOMPLETE_MODEL_OUTPUT", "S2_REVIEW_REQUIRED"}):
        raise ImportProblem("S2_SKIP_NOT_ALLOWED", "当前批次没有可跳过的单页识别失败", 409)
    checkpoint = dict(batch.checkpoint or {})
    s2 = dict(checkpoint.get("s2") or {})
    failed = s2.get("failed_unit") or {}
    units = list(s2.get("units") or [])
    index = failed.get("index")
    if (failed.get("code") != "INCOMPLETE_MODEL_OUTPUT"
            or not isinstance(index, int) or not 0 <= index < len(units)
            or units[index].get("index") != index):
        raise ImportProblem("S2_SKIP_NOT_ALLOWED", "失败页段记录不符合跳过条件", 409)
    unit = units[index]
    if (unit.get("status") != "pending" or unit.get("core_first_page") != page
            or unit.get("core_last_page") != page
            or any(row.get("status") != "completed" for row in units[:index])
            or any(row.get("status") != "pending" for row in units[index + 1:])):
        raise ImportProblem("S2_SKIP_NOT_ALLOWED", "仅能跳过当前失败的单页主体，请刷新后重试", 409)

    skip_error_message = batch.error_message
    # The queued worker will process remaining units; the skipped page stays visible for review.
    batch = retry_batch(db, batch_id, operator_token=operator_token)
    units[index] = {**unit, "status": "completed", "candidate_count": 0,
                    "skipped_by_teacher": True, "skipped_at": datetime.now(timezone.utc).isoformat(),
                    "skip_error_message": skip_error_message,
                    "unresolved_fragments": [],
                    "source_answers": []}
    s2["units"] = units
    s2.setdefault("recovery_history", []).append({"failed_index": index, "first_page": page,
                                                   "core_last_page": page, "action": "teacher_skip"})
    s2.pop("failed_unit", None)
    checkpoint["s2"] = s2
    batch.checkpoint = checkpoint
    return batch


def _extract_unit(batch_id: uuid.UUID, file_id: str, pdf, unit: PageUnit,
                  *, retry_invalid: bool = False,
                  include_answers: bool = False,
                  combined_prompt_version: str = COMBINED_PROMPT_VERSION,
                   document_page_roles: list[str] | None = None,
                   embedded_images: list[dict] | None = None) -> tuple[ModelOutput | CombinedModelOutput, dict]:
    artifact = _result_path(batch_id, file_id, unit.index)
    if artifact.is_file():
        data = json.loads(artifact.read_text(encoding="utf-8"))
        if "output" in data:
            output_model = combined_output_model(combined_prompt_version) if include_answers else ModelOutput
            return output_model.model_validate(data["output"]), data["meta"]
        try:
            return decode_qwen_response(data["raw_response"], page_count=unit.page_count,
                                        elapsed=data["elapsed_seconds"], model=get_settings().question_import_s2_model,
                                        include_answers=include_answers,
                                        combined_prompt_version=combined_prompt_version)
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

    kwargs = {"page_count": unit.page_count,
              "core_page_count": unit.core_last_page - unit.first_page + 1,
              "capture_response": capture_response}
    if include_answers:
        kwargs["include_answers"] = True
        if combined_prompt_version != COMBINED_PROMPT_VERSION:
            kwargs["combined_prompt_version"] = combined_prompt_version
        if document_page_roles and combined_prompt_version in {COMBINED_PROMPT_VERSION,
                                                               SECTIONED_COMBINED_PROMPT_VERSION,
                                                               INCLUSIVE_COMBINED_PROMPT_VERSION}:
            kwargs["page_roles"] = document_page_roles[unit.first_page - 1:unit.last_page]
    pdf_data = pdf_unit_bytes(pdf, unit)
    if embedded_images is not None:
        with fitz.open(stream=pdf_data, filetype="pdf") as document:
            kwargs["page_previews"] = [_render(page, page.rect, scale=1.5)[0] for page in document]
        kwargs["embedded_images"] = embedded_images
    output, meta, _ = provider.extract(pdf_data, **kwargs)
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
        embedded_inventory = None
        if s2.get("figure_version") == FIGURE_VERSION:
            with SessionLocal() as db:
                embedded_inventory = extract_images(db, batch_id, s2_worker_token=token)
                db.commit()
        document_page_roles = (pdf_page_roles(pdf) if batch.source_mode == "combined"
                               and s2.get("prompt_version") not in {
                                   FOCUSED_COMBINED_PROMPT_VERSION, COMBINED_PROMPT_VERSION,
                                   CLASSIFIED_COMBINED_PROMPT_VERSION,
                               } else [])
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
            valid_version = (
                COMBINED_PROMPT_VERSIONS
                if batch.source_mode == "combined" else {PROMPT_VERSION}
            )
            if s2.get("prompt_version") not in valid_version:
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
            include_answers = s2.get("prompt_version") in COMBINED_PROMPT_VERSIONS
            inclusive_combined = s2.get("prompt_version") == INCLUSIVE_COMBINED_PROMPT_VERSION
            reference_only = include_answers and not inclusive_combined and bool(document_page_roles) and all(
                role == "reference" for role in document_page_roles[unit.core_first_page - 1:unit.core_last_page]
            )
            if not reference_only and (s2.get("cost_unknown") or float(s2.get("estimated_cny") or 0) >= float(s2["budget_cny"])):
                _pause(batch_id, token, s2, "BUDGET_REACHED", "预算已达到或部分用量缺失，请核对账单后调整预算")
                return True
            if not reference_only and int(s2.get("calls") or 0) >= get_settings().question_import_s2_max_calls:
                _pause(batch_id, token, s2, "CALL_LIMIT_REACHED", "S2 单批调用次数达到上限")
                return True
            failed_unit = s2.get("failed_unit") or {}
            if reference_only:
                output = CombinedModelOutput(complete=True, questions=[], source_answers=[], unresolved_fragments=[])
                meta = {"usage": {}, "elapsed_seconds": 0.0, "response_id": None}
                cost = 0.0
            else:
                unit_images = ([{"asset_id": asset["asset_id"],
                                 "page": asset["page"] - unit.first_page + 1,
                                 "bbox": [round(value * 999) for value in asset["bbox"]]}
                                for asset in embedded_inventory["assets"]
                                if asset["kind"] == "embedded" and unit.first_page <= asset["page"] <= unit.last_page]
                               if embedded_inventory is not None else None)
                output, meta = _extract_unit(batch_id, source["file_id"], pdf, unit,
                                             retry_invalid=failed_unit.get("index") == unit.index
                                             and failed_unit.get("code") in {
                                                 "INVALID_MODEL_OUTPUT", "INVALID_MODEL_PAGE",
                                                 "INCOMPLETE_MODEL_OUTPUT",
                                                 "MODEL_OUTPUT_TRUNCATED",
                                             }, include_answers=include_answers,
                                             combined_prompt_version=s2.get("prompt_version", COMBINED_PROMPT_VERSION),
                                              document_page_roles=document_page_roles,
                                              embedded_images=unit_images)
                cost = estimate_cost(meta.get("usage") or {})
            with SessionLocal() as db:
                row = _locked_batch(db, batch_id, token)
                if row is None:
                    return True
                current = copy.deepcopy((row.checkpoint or {}).get("s2") or {})
                if current["units"][unit.index]["status"] == "completed":
                    continue
                existing_items = list(db.scalars(select(QuestionImportItem).where(
                    QuestionImportItem.batch_id == batch_id).with_for_update()))
                known = {candidate_key(item.payload) for item in existing_items}
                selected = 0
                overlap_duplicates = []
                overlap_suspects = []
                ignored_overlap_questions = []
                source_answers = []
                page_roles = document_page_roles if include_answers else []
                if include_answers:
                    for answer in output.source_answers:
                        if answer.page_start > unit.core_last_page - unit.first_page + 1:
                            continue
                        original_answer_page = unit.first_page + answer.page_start - 1
                        if not inclusive_combined and page_roles and page_roles[original_answer_page - 1] == "reference":
                            continue
                        source_answers.append({
                            "source_file_id": source["file_id"],
                            "original_number": answer.original_number,
                            "subquestion_number": answer.subquestion_number,
                            "text": answer.answer_text,
                            "page_start": unit.first_page + answer.page_start - 1,
                            "page_end": unit.first_page + answer.page_end - 1,
                            "uncertain_fragments": answer.uncertain_fragments,
                            "question_excerpt": answer.question_excerpt,
                            "answer_form": answer.answer_form,
                        })
                for question in output.questions:
                    original_page = unit.first_page + question.page_start - 1
                    if not inclusive_combined and page_roles and page_roles[original_page - 1] not in {"question", "mixed"}:
                        continue
                    if question.page_start > unit.core_last_page - unit.first_page + 1:
                        ignored_overlap_questions.append({"original_number": question.original_number,
                                                          "page_start": unit.first_page + question.page_start - 1})
                        continue
                    payload = candidate_from_question(source["file_id"], question, unit)
                    key = candidate_key(payload)
                    if key in known:
                        overlap_duplicates.append({"original_number": payload["source"]["original_number"],
                                                   "page_start": payload["source"]["page_start"],
                                                   "reason": "exact", "discarded_payload": payload})
                        continue
                    duplicate = next((item for item in existing_items if overlap_candidate_is_duplicate(
                        item.payload, payload, boundary_page=unit.first_page)), None)
                    if duplicate is not None:
                        payload["duplicate_status"] = "suspected"
                        payload["warnings"].append("与另一候选题疑似跨页重复；须核对原页后决定")
                    if embedded_inventory is not None:
                        asset_ids, figure_issues = question_figure_assets(
                            db, batch_id, token, source["file_id"], unit.first_page, question)
                        payload["content"]["asset_ids"] = asset_ids
                        payload["warnings"] = [warning for warning in payload["warnings"]
                                               if warning != "题图尚未裁剪；请对照原页复核"]
                        if asset_ids:
                            payload["warnings"].append("题图已自动关联，须核对原页和图片完整性")
                        elif question.figure_status != "no_figure" or question.diagram_clues:
                            payload["warnings"].append("题图尚未裁剪；请对照原页复核")
                        if figure_issues:
                            payload["warnings"].extend(figure_issues)
                        payload["extraction"]["figure_status"] = question.figure_status
                        payload["extraction"]["figure_issues"] = figure_issues
                    created = add_candidate(db, row, payload)
                    existing_items.append(created)
                    if duplicate is not None:
                        overlap_suspects.append({"candidate_item_id": str(created.id),
                                                 "other_item_id": str(duplicate.id),
                                                 "reason": "overlap_variant_requires_review"})
                    known.add(key)
                    selected += 1
                unresolved = [{"page": unit.first_page + fragment.page - 1,
                               "text": fragment.text, "reason": fragment.reason}
                              for fragment in output.unresolved_fragments
                              if not (unit.index > 0 and fragment.page == 1
                                      and re.match(r"^\s*[（(]?[A-Da-d](?:[)）.．、:：]|\s+)", fragment.text))]
                resolved_overlap = []
                if (s2.get("prompt_version") not in {FOCUSED_COMBINED_PROMPT_VERSION,
                                                      SECTIONED_COMBINED_PROMPT_VERSION,
                                                      INCLUSIVE_COMBINED_PROMPT_VERSION}
                        and not reference_only and not output.questions and not source_answers and not unresolved):
                    if not resolved_overlap:
                        unresolved = [{"page": unit.first_page, "text": "本页段没有识别出题目",
                                       "reason": "需核对原文件是否为空白、答案页或漏题"}]
                source_answer_signals = [
                    {"page": unit.first_page + signal.page - 1, "evidence": signal.evidence}
                    for signal in output.source_answer_signals
                    if signal.page <= unit.core_last_page - unit.first_page + 1
                ]
                current["units"][unit.index] = {**current["units"][unit.index], "status": "completed",
                                                  "candidate_count": selected,
                                                  "unresolved_fragments": unresolved,
                                                  "resolved_overlap_fragments": resolved_overlap,
                                                  "overlap_duplicates": overlap_duplicates,
                                                  "overlap_suspects": overlap_suspects,
                                                  "ignored_overlap_questions": ignored_overlap_questions,
                                                  "source_answers": source_answers,
                                                  "source_answer_signals": source_answer_signals,
                                                  "skipped_reference": reference_only,
                                                  "usage": meta.get("usage") or {},
                                                  "estimated_cny": cost,
                                                  "elapsed_seconds": meta["elapsed_seconds"],
                                                  "response_id": meta.get("response_id")}
                current["units"][unit.index].pop("ignored_nonquestion_sections", None)
                already_accounted = reference_only or bool(meta.get("response_id") and meta["response_id"] in
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
        logger.exception("S2 unit processing failed for batch %s", batch_id)
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

