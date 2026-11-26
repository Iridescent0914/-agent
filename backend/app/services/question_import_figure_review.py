"""Teacher decision that an extracted figure is fully represented in text."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import QuestionImportBatch, QuestionImportItem
from app.services.question_import import OWNER_SCOPE, ImportProblem, active_candidate


def figure_text_signature(payload: dict[str, Any]) -> str:
    content = payload.get("content") or {}
    extraction = payload.get("extraction") or {}
    relevant = {
        "question_text": content.get("question_text"),
        "options": content.get("options"),
        "diagram_clues": extraction.get("diagram_clues"),
        "figure_status": extraction.get("figure_status"),
    }
    return hashlib.sha256(json.dumps(relevant, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


def figure_text_confirmed(payload: dict[str, Any]) -> bool:
    decision = payload.get("figure_text_confirmation")
    return (isinstance(decision, dict)
            and decision.get("signature") == figure_text_signature(payload))


def set_figure_text_confirmation(db: Session, batch_id: uuid.UUID, item_id: uuid.UUID,
                                 *, expected_revision: int, confirmed: bool) -> QuestionImportItem:
    batch = db.scalar(select(QuestionImportBatch).where(
        QuestionImportBatch.id == batch_id, QuestionImportBatch.owner_scope == OWNER_SCOPE,
    ).with_for_update())
    if batch is None:
        raise ImportProblem("NOT_FOUND", "导入批次不存在", 404)
    if batch.status != "review_pending" or batch.stage != "s2_completed":
        raise ImportProblem("COMMIT_CONFLICT", "当前批次不可确认题图", 409)
    item = db.scalar(select(QuestionImportItem).where(
        QuestionImportItem.id == item_id, QuestionImportItem.batch_id == batch_id,
    ).with_for_update())
    if item is None or not active_candidate(item) or item.is_fake:
        raise ImportProblem("NOT_FOUND", "候选题不存在", 404)
    if item.revision != expected_revision:
        raise ImportProblem("REVISION_CONFLICT", "候选题已修改，请刷新", 409)
    payload = dict(item.payload or {})
    if (payload.get("content") or {}).get("asset_ids"):
        raise ImportProblem("INVALID_CANDIDATE", "题目已有题图，请先核对关联图片")
    extraction = payload.get("extraction") or {}
    if confirmed and not (extraction.get("diagram_clues") or
                          extraction.get("figure_status") in {"complete", "uncertain"}):
        raise ImportProblem("INVALID_CANDIDATE", "该题没有待确认的题图线索")
    if confirmed:
        payload["figure_text_confirmation"] = {
            "signature": figure_text_signature(payload),
            "confirmed_at": datetime.now(timezone.utc).isoformat(),
        }
    else:
        payload.pop("figure_text_confirmation", None)
    payload["teacher_decision"] = "pending"
    payload["commit_intent"] = None
    item.revision += 1
    payload["revision"] = item.revision
    item.payload = payload
    db.flush()
    return item
