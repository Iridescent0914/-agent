"""Private import staging shared by S1 and S2."""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import unicodedata
import uuid
import zipfile
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from typing import Any

from pypdf import PdfReader
from sqlalchemy import func, or_, select, text
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import KnowledgePoint, QuestionImportBatch, QuestionImportItem
from app.services.knowledge_catalog import CATALOG_PATH, formal_kp, formal_literacy_dimensions


OWNER_SCOPE = "internal-demo"
FINISHED = {"completed", "completed_with_unassigned", "cancelled", "failed"}
PURGEABLE_FOR_CAP = {"completed", "completed_with_unassigned", "cancelled"}
MODES = {"questions_only", "combined", "separate"}


def require_s2_operator_token(provided: str | None) -> None:
    expected = get_settings().question_import_s2_operator_token
    if not expected or not provided or not secrets.compare_digest(expected, provided):
        raise ImportProblem("S2_OPERATOR_REQUIRED", "S2 操作口令缺失或不正确", 403)


class ImportProblem(ValueError):
    def __init__(self, code: str, message: str, status_code: int = 422):
        super().__init__(message)
        self.code = code
        self.status_code = status_code


# Bump when DOCX rendering changes so an identical upload does not reuse an old batch.
DOCX_CONVERSION_VERSION = "libreoffice-math-omml-v2"


def content_fingerprint(question_text: str, options: object = None, assets: object = None) -> str:
    normalized = re.sub(r"\s+", " ", unicodedata.normalize("NFKC", question_text)).strip()
    value = json.dumps(
        {"text": normalized, "options": options or [], "assets": assets or []},
        sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str,
    )
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _validate_document(filename: str, data: bytes) -> tuple[str, int | None]:
    settings = get_settings()
    if not data or len(data) > settings.question_import_max_file_bytes:
        raise ImportProblem("FILE_LIMIT_EXCEEDED", "单个文件为空或超过大小限制", 413)
    suffix = Path(filename).suffix.lower()
    if suffix == ".pdf":
        if not data.startswith(b"%PDF-"):
            raise ImportProblem("INVALID_FILE", "PDF 文件头无效")
        try:
            reader = PdfReader(BytesIO(data), strict=True)
            if reader.is_encrypted:
                raise ImportProblem("INVALID_FILE", "不支持加密 PDF")
            pages = len(reader.pages)
        except ImportProblem:
            raise
        except Exception as exc:
            raise ImportProblem("INVALID_FILE", "PDF 结构损坏或不受支持") from exc
        if pages < 1 or pages > settings.question_import_max_pdf_pages:
            raise ImportProblem("FILE_LIMIT_EXCEEDED", "PDF 页数超过试点上限", 413)
        return "application/pdf", pages
    if suffix == ".docx":
        if not data.startswith(b"PK\x03\x04"):
            raise ImportProblem("INVALID_FILE", "DOCX 文件头无效")
        try:
            with zipfile.ZipFile(BytesIO(data)) as archive:
                infos = archive.infolist()
                names = {info.filename for info in infos}
                if "[Content_Types].xml" not in names or "word/document.xml" not in names:
                    raise ImportProblem("INVALID_FILE", "DOCX 缺少必要结构")
                if any(info.flag_bits & 1 for info in infos):
                    raise ImportProblem("INVALID_FILE", "不支持加密 DOCX")
                if any("vbaProject" in name or name.endswith(".bin") for name in names):
                    raise ImportProblem("INVALID_FILE", "不支持宏或二进制嵌入文档")
                if len(infos) > 5000 or sum(info.file_size for info in infos) > 300 * 1024 * 1024:
                    raise ImportProblem("FILE_LIMIT_EXCEEDED", "DOCX 解压内容超过安全上限", 413)
                if any(info.file_size > 30 * 1024 * 1024 or
                       (info.compress_size and info.file_size / info.compress_size > 1000)
                       for info in infos):
                    raise ImportProblem("INVALID_FILE", "DOCX 包含异常压缩内容")
                content_types = archive.read("[Content_Types].xml")
                if b"macroEnabled" in content_types:
                    raise ImportProblem("INVALID_FILE", "不支持宏文档")
        except ImportProblem:
            raise
        except (zipfile.BadZipFile, RuntimeError, OSError) as exc:
            raise ImportProblem("INVALID_FILE", "DOCX 结构损坏") from exc
        return "application/vnd.openxmlformats-officedocument.wordprocessingml.document", None
    raise ImportProblem("INVALID_FILE", "只支持 PDF 或 DOCX 文件")


def _path_for(batch_id: uuid.UUID, file_id: uuid.UUID) -> Path:
    return get_settings().question_import_dir / str(batch_id) / file_id.hex


def _purge_files(batch: QuestionImportBatch, *, now: datetime) -> None:
    if batch.files_purged_at is not None:
        return
    for item in batch.files:
        _path_for(batch.id, uuid.UUID(item["file_id"])).unlink(missing_ok=True)
    folder = get_settings().question_import_dir / str(batch.id)
    if folder.is_dir():
        for artifact in folder.iterdir():
            if artifact.is_file():
                artifact.unlink()
        folder.rmdir()
    batch.files_purged_at = now


def create_batch(db: Session, *, source_mode: str,
                 files: list[tuple[str, str, bytes]]) -> QuestionImportBatch:
    settings = get_settings()
    if source_mode not in MODES:
        raise ImportProblem("INVALID_FILE", "文件场景无效")
    if len(files) != (2 if source_mode == "separate" else 1):
        raise ImportProblem("INVALID_FILE", "文件数量与题答场景不符")
    if len(files) > settings.question_import_max_files or sum(len(data) for _, _, data in files) > settings.question_import_max_batch_bytes:
        raise ImportProblem("FILE_LIMIT_EXCEEDED", "批次文件超过试点上限", 413)
    validated = [(_validate_document(name, data), name, role, data) for role, name, data in files]
    if source_mode == "separate" and [role for _, _, role, _ in validated] != ["questions", "answers"]:
        raise ImportProblem("INVALID_FILE", "分文件场景需题目文件和答案文件")
    catalog_hash = hashlib.sha256(CATALOG_PATH.read_bytes()).hexdigest()
    fingerprint_data = {
        "mode": source_mode, "catalog": catalog_hash,
        "files": [(role, hashlib.sha256(data).hexdigest()) for _, _, role, data in validated],
    }
    if any(role == "questions" and media_type == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
           for (media_type, _), _, role, _ in validated):
        fingerprint_data["docx_conversion_version"] = DOCX_CONVERSION_VERSION
    request_hash = hashlib.sha256(json.dumps(fingerprint_data, sort_keys=True).encode()).hexdigest()
    # The transaction lock makes idempotency and the seven-batch limit atomic across workers.
    lock_key = 0x51494D504F5254  # One owner scope in the internal S1 deployment.
    db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock_key})
    existing = db.scalar(select(QuestionImportBatch).where(
        QuestionImportBatch.owner_scope == OWNER_SCOPE,
        QuestionImportBatch.request_fingerprint == request_hash,
        QuestionImportBatch.files_purged_at.is_(None),
    ))
    now = datetime.now(timezone.utc)
    if existing is not None:
        if existing.status != "cancelled" and existing.expires_at > now:
            existing.reused_existing = True
            return existing
        _purge_files(existing, now=now)
        db.flush()
    retained = list(db.scalars(select(QuestionImportBatch).where(
        QuestionImportBatch.owner_scope == OWNER_SCOPE,
        QuestionImportBatch.files_purged_at.is_(None),
    ).order_by(QuestionImportBatch.created_at, QuestionImportBatch.id).with_for_update()))
    if len(retained) >= settings.question_import_recent_batches:
        removable = next((row for row in retained if row.status in PURGEABLE_FOR_CAP), None)
        if removable is None:
            raise ImportProblem(
                "FILE_LIMIT_EXCEEDED",
                f"已有 {settings.question_import_recent_batches} 个批次保留原文件，且都不能自动清理；请取消不再使用的旧批次，或等待临时文件到期清理",
                409,
            )
        _purge_files(removable, now=now)
        db.flush()
    batch = QuestionImportBatch(
        id=uuid.uuid4(), owner_scope=OWNER_SCOPE, request_fingerprint=request_hash,
        catalog_snapshot_sha256=catalog_hash, source_mode=source_mode,
        status="uploaded", stage="files_saved", files=[], checkpoint={},
        expires_at=now + timedelta(hours=settings.question_import_retention_hours),
    )
    db.add(batch)
    db.flush()
    folder = settings.question_import_dir / str(batch.id)
    folder.mkdir(parents=True, exist_ok=False)
    manifest = []
    try:
        for (media_type, pages), name, role, data in validated:
            file_id = uuid.uuid4()
            path = _path_for(batch.id, file_id)
            with path.open("xb") as output:
                output.write(data)
            manifest.append({
                "file_id": str(file_id), "role": role,
                "original_name": name.replace("\\", "/").split("/")[-1][:255],
                "media_type": media_type, "size_bytes": len(data), "page_count": pages,
                "sha256": hashlib.sha256(data).hexdigest(),
            })
    except Exception:
        for item in manifest:
            _path_for(batch.id, uuid.UUID(item["file_id"])).unlink(missing_ok=True)
        folder.rmdir()
        raise
    batch.files = manifest
    batch.reused_existing = False
    try:
        db.flush()
    except Exception:
        for item in manifest:
            _path_for(batch.id, uuid.UUID(item["file_id"])).unlink(missing_ok=True)
        folder.rmdir()
        raise
    return batch


def get_batch(db: Session, batch_id: uuid.UUID) -> QuestionImportBatch:
    batch = db.get(QuestionImportBatch, batch_id)
    if batch is None or batch.owner_scope != OWNER_SCOPE:
        raise ImportProblem("NOT_FOUND", "导入批次不存在", 404)
    return batch


def cancel_batch(db: Session, batch_id: uuid.UUID) -> QuestionImportBatch:
    batch = db.scalar(select(QuestionImportBatch).where(
        QuestionImportBatch.id == batch_id, QuestionImportBatch.owner_scope == OWNER_SCOPE,
    ).with_for_update())
    if batch is None:
        raise ImportProblem("NOT_FOUND", "导入批次不存在", 404)
    if batch.status in {"completed", "completed_with_unassigned"}:
        raise ImportProblem("COMMIT_CONFLICT", "已完成的批次不能取消", 409)
    batch.status = "cancelled"
    batch.stage = "cancelled"
    batch.worker_token = None
    batch.lease_until = None
    return batch


def retry_batch(db: Session, batch_id: uuid.UUID, *, operator_token: str | None = None) -> QuestionImportBatch:
    batch = db.scalar(select(QuestionImportBatch).where(
        QuestionImportBatch.id == batch_id, QuestionImportBatch.owner_scope == OWNER_SCOPE,
    ).with_for_update())
    if batch is None:
        raise ImportProblem("NOT_FOUND", "导入批次不存在", 404)
    if batch.status != "failed" or batch.files_purged_at is not None or batch.expires_at <= datetime.now(timezone.utc):
        raise ImportProblem("COMMIT_CONFLICT", "该批次无法重试，请重新上传", 409)
    if batch.stage == "s2_failed":
        require_s2_operator_token(operator_token)
        settings = get_settings()
        if not settings.question_import_s2_enabled or not settings.question_import_s2_api_key or not settings.question_import_s2_base_url.startswith("https://"):
            raise ImportProblem("S2_NOT_CONFIGURED", "S2 百炼调用尚未在服务端配置", 409)
    batch.status = "uploaded"
    batch.stage = "s2_queued" if batch.stage == "s2_failed" else "files_saved"
    batch.error_code = batch.error_message = None
    batch.worker_token = None
    batch.lease_until = None
    batch.attempt_count = 0
    return batch


def add_candidate(db: Session, batch: QuestionImportBatch, payload: dict[str, Any], *, fake: bool = False) -> QuestionImportItem:
    batch = db.scalar(select(QuestionImportBatch).where(
        QuestionImportBatch.id == batch.id,
        QuestionImportBatch.owner_scope == OWNER_SCOPE,
    ).with_for_update())
    if batch is None:
        raise ImportProblem("NOT_FOUND", "导入批次不存在", 404)
    count = db.scalar(select(func.count(QuestionImportItem.id)).where(QuestionImportItem.batch_id == batch.id)) or 0
    if count >= get_settings().question_import_max_candidates:
        raise ImportProblem("FILE_LIMIT_EXCEEDED", "候选题数量超过试点上限", 413)
    item_id = uuid.uuid4()
    payload = dict(payload)
    payload["batch_id"] = str(batch.id)
    payload["item_id"] = str(item_id)
    payload["revision"] = 1
    payload["catalog_snapshot_sha256"] = batch.catalog_snapshot_sha256
    tags = dict(payload.get("tags") or {})
    tags["is_required"] = False
    payload["tags"] = tags
    next_ordinal = (db.scalar(select(func.max(QuestionImportItem.ordinal)).where(
        QuestionImportItem.batch_id == batch.id)) or 0) + 1
    item = QuestionImportItem(id=item_id, batch_id=batch.id, ordinal=next_ordinal,
                              revision=1, payload=payload, is_fake=fake)
    db.add(item)
    db.flush()
    return item


def update_candidate(db: Session, batch_id: uuid.UUID, item_id: uuid.UUID,
                     *, expected_revision: int, changes: dict[str, Any]) -> QuestionImportItem:
    batch = get_batch(db, batch_id)
    if batch.status in {"cancelled", "completed", "completed_with_unassigned"}:
        raise ImportProblem("COMMIT_CONFLICT", "批次当前不可编辑", 409)
    item = db.scalar(select(QuestionImportItem).where(
        QuestionImportItem.id == item_id, QuestionImportItem.batch_id == batch.id,
    ).with_for_update())
    if item is None:
        raise ImportProblem("NOT_FOUND", "候选题不存在", 404)
    if item.revision != expected_revision:
        raise ImportProblem("REVISION_CONFLICT", "候选题已被修改，请刷新", 409)
    if batch.catalog_snapshot_sha256 != hashlib.sha256(CATALOG_PATH.read_bytes()).hexdigest():
        raise ImportProblem("CATALOG_CHANGED", "正式标签目录已变化，请重新上传批次", 409)
    allowed = {"content", "tags", "answers", "selected_answer_id", "teacher_decision", "commit_intent", "warnings"}
    if not changes or set(changes) - allowed:
        raise ImportProblem("INVALID_CANDIDATE", "候选题修改字段无效")
    payload = dict(item.payload)
    if "teacher_decision" in changes and changes["teacher_decision"] not in {
        "pending", "approved", "rejected", "skipped_duplicate"
    }:
        raise ImportProblem("INVALID_CANDIDATE", "候选题决定无效")
    if "commit_intent" in changes and changes["commit_intent"] not in {
        "library_only", "direct_pool"
    }:
        raise ImportProblem("INVALID_CANDIDATE", "提交意向无效")
    if "content" in changes and (
        not isinstance(changes["content"], dict)
        or not isinstance(changes["content"].get("question_text"), str)
        or not isinstance(changes["content"].get("options"), list)
        or not isinstance(changes["content"].get("asset_ids"), list)
    ):
        raise ImportProblem("INVALID_CANDIDATE", "题目内容结构无效")
    if "answers" in changes and (
        not isinstance(changes["answers"], list)
        or any(not isinstance(value, dict) or not isinstance(value.get("answer_id"), str)
               for value in changes["answers"])
    ):
        raise ImportProblem("INVALID_CANDIDATE", "答案版本结构无效")
    if "warnings" in changes and (
        not isinstance(changes["warnings"], list)
        or any(not isinstance(value, str) for value in changes["warnings"])
    ):
        raise ImportProblem("INVALID_CANDIDATE", "提示信息结构无效")
    if "tags" in changes:
        submitted = changes["tags"]
        if not isinstance(submitted, dict) or set(submitted) - {
            "question_type", "difficulty", "main_kp_code", "is_required"
        }:
            raise ImportProblem("TAG_CONFLICT", "章、年级、节和素养只能由主属 KP 派生")
        if "is_required" in submitted and not isinstance(submitted["is_required"], bool):
            raise ImportProblem("INVALID_CANDIDATE", "必做标记必须是布尔值")
        if "question_type" in submitted and submitted["question_type"] not in {
            "choice", "fill_blank", "calculation", "solution", "true_false", "unknown"
        }:
            raise ImportProblem("INVALID_CANDIDATE", "题型无效")
        if "difficulty" in submitted and (
            submitted["difficulty"] is not None
            and (type(submitted["difficulty"]) is not int or not 1 <= submitted["difficulty"] <= 5)
        ):
            raise ImportProblem("INVALID_CANDIDATE", "难度必须为 1～5 星")
        tags = {**payload.get("tags", {}), **submitted}
        kp_code = tags.get("main_kp_code")
        if kp_code is not None and not isinstance(kp_code, str):
            raise ImportProblem("INVALID_CANDIDATE", "主属知识点编码无效")
        if kp_code:
            try:
                kp = formal_kp(kp_code)
            except ValueError as exc:
                raise ImportProblem("TAG_OUT_OF_SCOPE", "知识点不在正式目录内") from exc
            exists = db.scalar(select(KnowledgePoint.id).where(KnowledgePoint.code == kp_code))
            if exists is None or not kp.get("available_for_import", True):
                raise ImportProblem("TAG_OUT_OF_SCOPE", "知识点尚未开放入库")
            literacies = kp["core_competencies"]
            if not literacies or any(name not in formal_literacy_dimensions() for name in literacies):
                raise ImportProblem("TAG_CONFLICT", "知识点素养映射不完整")
            tags.update({"grade": kp["grade"], "chapter_code": kp["chapter_code"],
                         "section": kp["section"], "core_literacies": literacies})
        else:
            tags.update({"grade": None, "chapter_code": None, "section": None,
                         "core_literacies": []})
        payload["tags"] = tags
    payload.update({key: value for key, value in changes.items() if key != "tags"})
    # Model metadata, source evidence, batch identity and fake marker are immutable here.
    answers = payload.get("answers", [])
    if not isinstance(answers, list) or any(not isinstance(answer, dict) for answer in answers):
        raise ImportProblem("INVALID_CANDIDATE", "答案版本结构无效")
    if payload.get("selected_answer_id") is not None and payload["selected_answer_id"] not in {
        answer.get("answer_id") for answer in answers
    }:
        raise ImportProblem("INVALID_CANDIDATE", "所选答案版本不存在")
    item.revision += 1
    payload["revision"] = item.revision
    item.payload = payload
    return item


def purge_expired_import_files(db: Session, *, now: datetime | None = None) -> int:
    current = now or datetime.now(timezone.utc)
    rows = list(db.scalars(select(QuestionImportBatch).where(
        QuestionImportBatch.files_purged_at.is_(None),
        QuestionImportBatch.expires_at <= current,
    ).with_for_update(skip_locked=True)))
    for batch in rows:
        _purge_files(batch, now=current)
        if batch.status not in FINISHED:
            batch.status = "failed"
            batch.stage = "expired"
            batch.error_code = "FILE_EXPIRED"
            batch.error_message = "临时文件已过期，请重新上传"
            batch.worker_token = None
            batch.lease_until = None
    return len(rows)
