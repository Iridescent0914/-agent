"""Restart-safe import worker for S1 validation and explicit S2 extraction."""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.models import QuestionImportBatch
from app.services.question_import import OWNER_SCOPE, _path_for


def fake_candidate(batch_id: uuid.UUID, ordinal: int = 1) -> dict:
    """Deterministic fixture for API/UI tests; never used for formal import."""
    return {
        "schema_version": "import-candidate-v1",
        "batch_id": str(batch_id), "item_id": str(uuid.uuid5(batch_id, str(ordinal))),
        "revision": 1,
        "source": {"file_id": str(uuid.uuid5(batch_id, "fake-file")),
                   "original_number": str(ordinal), "page_start": 1,
                   "page_end": 1, "evidence": []},
        "content": {"question_text": f"[FAKE] S1 候选题 {ordinal}", "options": [], "solution": None, "asset_ids": []},
        "tags": {"question_type": "unknown", "difficulty": None, "grade": None,
                 "chapter_code": None, "section": None, "main_kp_code": None,
                 "core_literacies": [], "is_required": False},
        "answers": [], "selected_answer_id": None,
        "answer_match_status": "not_applicable", "precheck_status": "not_run",
        "label_status": "needs_confirmation", "teacher_decision": "pending",
        "commit_intent": "library_only", "duplicate_status": "none",
        "field_confidence": {}, "field_evidence": {},
        "warnings": ["fake_provider_result"],
    }


class FakeImportProvider:
    """Explicit offline adapter for deterministic S1 tests and UI fixtures."""

    provider_name = "fake-s1"

    def extract(self, batch_id: uuid.UUID, *, count: int = 1) -> list[dict]:
        return [fake_candidate(batch_id, ordinal) for ordinal in range(1, count + 1)]


def claim_one(db: Session, *, now: datetime | None = None) -> tuple[uuid.UUID, uuid.UUID] | None:
    current = now or datetime.now(timezone.utc)
    exhausted = db.scalar(select(QuestionImportBatch).where(
        QuestionImportBatch.owner_scope == OWNER_SCOPE,
        QuestionImportBatch.status == "parsing",
        QuestionImportBatch.stage == "validating_files",
        QuestionImportBatch.lease_until < current,
        QuestionImportBatch.attempt_count >= get_settings().question_import_max_attempts,
    ).order_by(QuestionImportBatch.created_at).with_for_update(skip_locked=True))
    if exhausted is not None:
        exhausted.status = "failed"
        exhausted.stage = "file_validation_failed"
        exhausted.error_code = "WORKER_TIMEOUT"
        exhausted.error_message = "文件校验任务多次超时，可手动重试"
        exhausted.worker_token = None
        exhausted.lease_until = None
    row = db.scalar(select(QuestionImportBatch).where(
        QuestionImportBatch.owner_scope == OWNER_SCOPE,
        QuestionImportBatch.files_purged_at.is_(None),
        QuestionImportBatch.expires_at > current,
        or_(
            (QuestionImportBatch.status == "uploaded") & (QuestionImportBatch.stage == "files_saved"),
            (QuestionImportBatch.status == "parsing") &
            (QuestionImportBatch.stage == "validating_files") &
            (QuestionImportBatch.lease_until < current) &
            (QuestionImportBatch.attempt_count < get_settings().question_import_max_attempts),
        ),
    ).order_by(QuestionImportBatch.created_at, QuestionImportBatch.id).with_for_update(skip_locked=True))
    if row is None:
        return None
    token = uuid.uuid4()
    row.status = "parsing"
    row.stage = "validating_files"
    row.worker_token = token
    row.heartbeat_at = current
    row.lease_until = current + timedelta(seconds=get_settings().question_import_lease_seconds)
    row.attempt_count += 1
    db.flush()
    return row.id, token


def heartbeat(db: Session, batch_id: uuid.UUID, token: uuid.UUID) -> None:
    row = db.scalar(select(QuestionImportBatch).where(
        QuestionImportBatch.id == batch_id,
        QuestionImportBatch.worker_token == token,
        QuestionImportBatch.status == "parsing",
    ).with_for_update())
    if row is None:
        raise RuntimeError("worker lease lost")
    now = datetime.now(timezone.utc)
    row.heartbeat_at = now
    row.lease_until = now + timedelta(seconds=get_settings().question_import_lease_seconds)


def _validate_saved_file(batch_id: uuid.UUID, item: dict) -> dict:
    path = _path_for(batch_id, uuid.UUID(item["file_id"]))
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
            size += len(block)
    if digest.hexdigest() != item["sha256"] or size != item["size_bytes"]:
        raise RuntimeError("saved file checksum mismatch")
    return {"file_id": item["file_id"], "sha256": digest.hexdigest()}


def process_one() -> bool:
    with SessionLocal() as db:
        claimed = claim_one(db)
        db.commit()
    if claimed is None:
        from app.services.question_import_s2_worker import process_s2_one
        if process_s2_one():
            return True
        from app.services.question_import_s3_global import process_global_dedupe_one
        if process_global_dedupe_one():
            return True
        from app.services.question_import_s3_separate import process_separate_answers_one
        if process_separate_answers_one():
            return True
        from app.services.question_import_s3_answer_match import process_answer_matching_one
        if process_answer_matching_one():
            return True
        from app.services.question_import_s3_labels import process_labels_one
        return process_labels_one()
    batch_id, token = claimed
    try:
        with SessionLocal() as db:
            batch = db.get(QuestionImportBatch, batch_id)
            if batch is None or not batch.files:
                raise RuntimeError("batch has no saved files")
            files = list(batch.files)
            validated = list((batch.checkpoint or {}).get("files", []))
        completed_ids = {item["file_id"] for item in validated}
        for item in files:
            if item["file_id"] in completed_ids:
                continue
            result = _validate_saved_file(batch_id, item)
            with SessionLocal() as db:
                row = db.scalar(select(QuestionImportBatch).where(
                    QuestionImportBatch.id == batch_id,
                    QuestionImportBatch.worker_token == token,
                    QuestionImportBatch.status == "parsing",
                ).with_for_update())
                if row is None:
                    return True
                validated.append(result)
                row.checkpoint = {"step": "validating_files", "files": validated}
                heartbeat(db, batch_id, token)
                db.commit()
        with SessionLocal() as db:
            row = db.scalar(select(QuestionImportBatch).where(
                QuestionImportBatch.id == batch_id,
                QuestionImportBatch.worker_token == token,
                QuestionImportBatch.status == "parsing",
            ).with_for_update())
            if row is None:
                return True
            row.checkpoint = {"step": "files_validated", "files": validated}
            row.stage = "awaiting_s2"
            row.status = "uploaded"
            row.worker_token = None
            row.lease_until = None
            db.commit()
    except Exception as exc:
        with SessionLocal() as db:
            row = db.scalar(select(QuestionImportBatch).where(
                QuestionImportBatch.id == batch_id,
                QuestionImportBatch.worker_token == token,
            ).with_for_update())
            if row is not None:
                row.status = "failed"
                row.stage = "file_validation_failed"
                row.error_code = "INVALID_FILE"
                row.error_message = str(exc)[:240]
                row.worker_token = None
                row.lease_until = None
                db.commit()
    return True
