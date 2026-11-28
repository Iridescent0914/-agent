from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Question
from app.schemas.question_import_s6 import S6PreflightRequest
from app.services.question_import_s4_exact import exact_catalog_matches
from app.services.question_import_s5_preflight import _context
from app.services.question_import_s5_review import read_item_reviews


def _problem(code: str, message: str) -> dict[str, str]:
    return {"code": code, "message": message}


def preflight_commit(db: Session, batch_id: uuid.UUID,
                     request: S6PreflightRequest) -> dict[str, Any]:
    """Read-only S6 input check; the eventual write must repeat it under locks."""
    ctx = _context(db, batch_id)
    reviews = {row["item_id"]: row for row in read_item_reviews(db, batch_id)["items"]}
    exact_result = exact_catalog_matches(db, batch_id)
    exact = {row["item_id"]: row for row in exact_result["results"]}
    selected_ids = {row.item_id for row in request.items}
    already_imported = set(db.scalars(select(Question.import_item_id).where(
        Question.import_item_id.in_(selected_ids)
    )))
    fingerprints: dict[str, list[str]] = {}
    for selection in request.items:
        match = exact.get(str(selection.item_id))
        if match and match["fingerprint"]:
            fingerprints.setdefault(match["fingerprint"], []).append(str(selection.item_id))

    rows: list[dict[str, Any]] = []
    for selection in request.items:
        item = ctx["item_by_id"].get(selection.item_id)
        review = reviews.get(str(selection.item_id))
        match = exact.get(str(selection.item_id))
        blockers: list[dict[str, str]] = []
        warnings: list[dict[str, str]] = []
        if item is None or match is None:
            blockers.append(_problem("CANDIDATE_UNAVAILABLE", "候选题不存在或不可提交"))
        elif item.revision != selection.expected_revision:
            blockers.append(_problem("REVISION_CONFLICT", "候选题版本已变化，请刷新后重新核对"))
        if not review or not review.get("valid") or review.get("decision") != "approved":
            blockers.append(_problem("REVIEW_NOT_APPROVED", "缺少当前有效的教师入库审核"))
        elif review.get("question_event_id") != str(selection.question_event_id):
            blockers.append(_problem("REVIEW_CHANGED", "教师审核记录已变化，请刷新拟入库清单"))
        if selection.item_id in already_imported:
            blockers.append(_problem("ALREADY_IMPORTED", "该候选题已有正式入库记录"))
        if match is not None:
            if match["matches"]:
                blockers.append(_problem("EXACT_DUPLICATE", "正式题库已出现精确重复题，请重新检查题库重复"))
            if match["fingerprint"] and len(fingerprints[match["fingerprint"]]) > 1:
                blockers.append(_problem("EXACT_DUPLICATE_IN_SELECTION", "本次选择中有精确重复题"))
            if match["status"] == "unverifiable":
                warnings.append(_problem("DUPLICATE_CHECK_UNVERIFIABLE", "此题内容无法计算精确指纹，提交前需人工核对"))
            if exact_result["unindexed_catalog_count"]:
                warnings.append(_problem("UNINDEXED_CATALOG", "正式题库仍有未建立精确指纹的题，当前检索不能证明没有重复"))
        rows.append({
            "item_id": str(selection.item_id),
            "ordinal": item.ordinal if item else None,
            "expected_revision": selection.expected_revision,
            "current_revision": item.revision if item else None,
            "question_event_id": str(selection.question_event_id),
            "fingerprint": match["fingerprint"] if match else None,
            "route": review.get("route") if review and review.get("valid") else None,
            "blockers": blockers, "warnings": warnings,
            "eligible": not blockers,
        })
    return {
        "batch_id": str(batch_id), "check_kind": "s6-commit-readiness",
        "items": rows,
        "summary": {
            "selected": len(rows),
            "eligible": sum(row["eligible"] for row in rows),
            "blocked": sum(not row["eligible"] for row in rows),
            "library_only": sum(row["eligible"] and row["route"] == "library_only" for row in rows),
            "direct_pool": sum(row["eligible"] and row["route"] == "direct_pool" for row in rows),
        },
    }
