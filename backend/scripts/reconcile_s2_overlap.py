"""Audit or repair overlap artifacts in one completed S2 import batch.

Run ``python -m scripts.reconcile_s2_overlap BATCH_ID`` first. Add ``--apply``
only after checking the proposed item IDs. Removed candidates are retained in
the batch checkpoint so the operation can be reviewed or reversed.
"""

from __future__ import annotations

import argparse
import copy
import json
import uuid
from difflib import SequenceMatcher

from sqlalchemy import select

from app.core.database import SessionLocal
from app.models.entities import QuestionImportBatch, QuestionImportItem
from app.services.question_import_s2 import (
    _comparison_text, overlap_candidate_is_duplicate, prefer_incoming_overlap, resolved_overlap_option,
)


def audit(batch: QuestionImportBatch, items: list[QuestionImportItem]) -> tuple[list[dict], list[dict]]:
    s2 = (batch.checkpoint or {}).get("s2") or {}
    units = s2.get("units") or []
    if batch.stage != "s2_completed" or batch.status != "review_pending" or not units:
        raise ValueError("Batch must be a completed S2 batch awaiting review")
    if sum(int(unit.get("candidate_count") or 0) for unit in units) != len(items):
        raise ValueError("Unit candidate counts differ from item count; manual review required")
    if [item.ordinal for item in items] != list(range(1, len(items) + 1)):
        raise ValueError("Item ordinals are not contiguous; manual review required")

    unit_items: list[list[QuestionImportItem]] = []
    offset = 0
    for unit in units:
        count = int(unit.get("candidate_count") or 0)
        unit_items.append(items[offset:offset + count])
        offset += count

    item_unit = {item.id: index for index, group in enumerate(unit_items) for item in group}
    duplicates: list[dict] = []
    removed_ids: set[uuid.UUID] = set()
    resolved: list[dict] = []
    seen: list[QuestionImportItem] = []
    for index, group in enumerate(unit_items):
        boundary = int(units[index]["first_page"])
        previous = [item.payload for item in seen]
        for item in group:
            match = next((old for old in seen if overlap_candidate_is_duplicate(
                old.payload, item.payload, boundary_page=boundary)), None)
            if match is None:
                seen.append(item)
                continue
            if any(row.revision != 1 or row.payload.get("teacher_decision") != "pending"
                   for row in (match, item)):
                raise ValueError(f"Duplicate candidate {item.id} has teacher changes; manual review required")
            replace = prefer_incoming_overlap(match.payload, item.payload)
            kept, removed = (item, match) if replace else (match, item)
            duplicates.append({"unit_index": item_unit[removed.id], "kept_item_id": str(kept.id),
                                   "removed_item_id": str(removed.id), "removed_ordinal": removed.ordinal,
                                   "original_number": removed.payload["source"].get("original_number"),
                                   "text_similarity": round(SequenceMatcher(None,
                                       _comparison_text(match.payload["content"]["question_text"]),
                                       _comparison_text(item.payload["content"]["question_text"]),
                                       autojunk=False).ratio(), 3),
                                   "kept_text_length": len(kept.payload["content"]["question_text"]),
                                   "removed_text_length": len(removed.payload["content"]["question_text"]),
                                   "payload": copy.deepcopy(removed.payload)})
            removed_ids.add(removed.id)
            if replace:
                seen.remove(match)
                seen.append(item)
        for fragment in units[index].get("unresolved_fragments") or []:
            if resolved_overlap_option(fragment, boundary_page=boundary,
                                       previous=previous):
                resolved.append({"unit_index": index, "fragment": copy.deepcopy(fragment)})
    return duplicates, resolved


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("batch_id", type=uuid.UUID)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    with SessionLocal.begin() as db:
        batch = db.scalar(select(QuestionImportBatch).where(
            QuestionImportBatch.id == args.batch_id).with_for_update())
        if batch is None:
            raise ValueError("Batch not found")
        items = list(db.scalars(select(QuestionImportItem).where(
            QuestionImportItem.batch_id == batch.id).order_by(QuestionImportItem.ordinal).with_for_update()))
        duplicates, resolved = audit(batch, items)
        report = {"batch_id": str(batch.id), "duplicate_pairs": [
            {key: value for key, value in row.items() if key != "payload"} for row in duplicates],
            "resolved_fragments": resolved}
        print(json.dumps(report, ensure_ascii=False, indent=2))
        if not args.apply or (not duplicates and not resolved):
            return
        checkpoint = copy.deepcopy(batch.checkpoint)
        s2 = checkpoint["s2"]
        removed_ids = {uuid.UUID(row["removed_item_id"]) for row in duplicates}
        for row in duplicates:
            unit = s2["units"][row["unit_index"]]
            unit["candidate_count"] -= 1
            unit.setdefault("overlap_duplicates", []).append({
                "kept_item_id": row["kept_item_id"], "removed_item_id": row["removed_item_id"],
                "reason": "retrospective_overlap_variant"})
        for row in resolved:
            unit = s2["units"][row["unit_index"]]
            unit["unresolved_fragments"].remove(row["fragment"])
            unit.setdefault("resolved_overlap_fragments", []).append(row["fragment"])
        reconciliation = s2.setdefault("overlap_reconciliation", {
            "removed_candidates": [], "resolved_fragments": []})
        reconciliation["removed_candidates"].extend(duplicates)
        reconciliation["resolved_fragments"].extend(resolved)
        for item in items:
            if item.id in removed_ids:
                db.delete(item)
        db.flush()
        remaining = [item for item in items if item.id not in removed_ids]
        for item in remaining:
            item.ordinal = -item.ordinal
        db.flush()
        for ordinal, item in enumerate(remaining, start=1):
            item.ordinal = ordinal
        batch.checkpoint = checkpoint
    if args.apply and (duplicates or resolved):
        print(f"Applied: removed {len(duplicates)} duplicates, resolved {len(resolved)} fragments")


if __name__ == "__main__":
    main()
