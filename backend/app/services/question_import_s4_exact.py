"""Read-only S4 lookup against the indexed formal question catalog."""

from __future__ import annotations

import hashlib
import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Question, QuestionImportItem
from app.services.question_fingerprint_v2 import VERSION, catalog_content_key, content_key, fingerprint, normalized_text
from app.services.question_import import ImportProblem, active_candidate, get_batch
from app.services.question_import_s3_images import image_bytes


def _candidate_key(db: Session, batch_id: uuid.UUID, item: QuestionImportItem) -> dict[str, Any] | None:
    payload = item.payload or {}
    content = payload.get("content") or {}
    asset_ids = content.get("asset_ids") or []
    if not isinstance(asset_ids, list):
        return None
    extraction = payload.get("extraction") or {}
    if (extraction.get("figure_status") == "uncertain"
            or (not asset_ids and (extraction.get("diagram_clues")
                                   or extraction.get("figure_status") == "complete"))):
        return None
    hashes: list[str] = []
    for raw_id in asset_ids:
        try:
            data = image_bytes(db, batch_id, uuid.UUID(str(raw_id)))
        except (ImportProblem, ValueError, TypeError):
            return None
        hashes.append(hashlib.sha256(data).hexdigest())
    return content_key(content.get("question_text"), content.get("options"), hashes)


def exact_catalog_matches(db: Session, batch_id: uuid.UUID) -> dict[str, Any]:
    batch = get_batch(db, batch_id)
    s3 = (batch.checkpoint or {}).get("s3") or {}
    if (batch.status != "review_pending" or batch.stage != "s2_completed"
            or (s3.get("global_dedupe") or {}).get("status") != "completed"):
        raise ImportProblem("COMMIT_CONFLICT", "请先完成 S3 整批去重", 409)

    items = [item for item in db.scalars(select(QuestionImportItem).where(
        QuestionImportItem.batch_id == batch_id
    ).order_by(QuestionImportItem.ordinal)) if active_candidate(item) and not item.is_fake]
    keys = {item.id: _candidate_key(db, batch_id, item) for item in items}
    hashes = {item_id: fingerprint(key) for item_id, key in keys.items()}
    wanted = {value for value in hashes.values() if value is not None}
    matches_by_hash: dict[str, list[Question]] = {}
    if wanted:
        # The versioned B-tree column is the only catalog lookup predicate.
        for question in db.scalars(select(Question).where(
            Question.exact_fingerprint_v2.in_(wanted), Question.archived_at.is_(None)
        ).order_by(Question.original_qid, Question.id)):
            matches_by_hash.setdefault(question.exact_fingerprint_v2, []).append(question)

    results: list[dict[str, Any]] = []
    for item in items:
        key = keys[item.id]
        value = hashes[item.id]
        payload = item.payload or {}
        selected_answer = next((answer.get("text") for answer in payload.get("answers") or []
                                if answer.get("answer_id") == payload.get("selected_answer_id")), None)
        matches: list[dict[str, Any]] = []
        stale_catalog_rows = 0
        if value is not None:
            for question in matches_by_hash.get(value, []):
                current_key = catalog_content_key(question.question_text, question.options,
                                                  question.assets, has_image=question.has_image)
                if current_key != key:
                    stale_catalog_rows += 1
                    continue
                matches.append({
                    "question_id": str(question.id),
                    "original_qid": question.original_qid,
                    "question_text": question.question_text,
                    "options": question.options,
                    "reference_answer_raw": question.reference_answer_raw,
                    "answer_text_differs": (normalized_text(selected_answer) != normalized_text(question.reference_answer_raw)
                                            if isinstance(selected_answer, str) and selected_answer.strip()
                                            and question.reference_answer_raw.strip() else None),
                    "review_revision": question.review_revision,
                })
        results.append({
            "item_id": str(item.id), "revision": item.revision, "fingerprint": value,
            "status": "unverifiable" if value is None else ("exact_matches" if matches else "no_exact_match"),
            "matches": matches, "stale_catalog_rows": stale_catalog_rows,
        })

    unindexed_count = db.scalar(select(func.count()).select_from(Question).where(
        Question.exact_fingerprint_v2.is_(None), Question.archived_at.is_(None)
    )) or 0
    return {"version": VERSION, "batch_id": str(batch_id), "results": results,
            "unindexed_catalog_count": unindexed_count}
