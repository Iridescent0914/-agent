"""Audit overlap artifacts in one completed S2 import batch.

``--apply`` records suspected pairs and fragments for review. It never deletes
or replaces a candidate based on a text-similarity threshold.
"""

from __future__ import annotations

import argparse
import json
import uuid

from sqlalchemy import select

from app.core.database import SessionLocal
from app.models.entities import QuestionImportBatch, QuestionImportItem
from app.services.question_import_s2 import overlap_candidate_is_duplicate, resolved_overlap_option


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

    suspects: list[dict] = []
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
            suspects.append({"unit_index": index, "candidate_item_id": str(item.id),
                             "other_item_id": str(match.id),
                             "reason": "overlap_variant_requires_review"})
            seen.append(item)
        for fragment in units[index].get("unresolved_fragments") or []:
            if resolved_overlap_option(fragment, boundary_page=boundary,
                                       previous=previous):
                resolved.append({"unit_index": index, "fragment": fragment,
                                 "status": "suggested_requires_review"})
    return suspects, resolved


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
        suspects, resolved = audit(batch, items)
        report = {"batch_id": str(batch.id), "suspected_pairs": suspects,
                  "suggested_fragments": resolved}
        print(json.dumps(report, ensure_ascii=False, indent=2))
        if not args.apply or (not suspects and not resolved):
            return
        checkpoint = dict(batch.checkpoint)
        s2 = checkpoint["s2"]
        for row in suspects:
            unit = s2["units"][row["unit_index"]]
            existing = unit.setdefault("overlap_suspects", [])
            if not any(pair.get("candidate_item_id") == row["candidate_item_id"]
                       and pair.get("other_item_id") == row["other_item_id"] for pair in existing):
                existing.append({key: value for key, value in row.items() if key != "unit_index"})
        for row in resolved:
            unit = s2["units"][row["unit_index"]]
            suggestion = {**row["fragment"], "status": "suggested_requires_review"}
            if suggestion not in unit.setdefault("resolved_overlap_fragments", []):
                unit["resolved_overlap_fragments"].append(suggestion)
        batch.checkpoint = checkpoint
    if args.apply and (suspects or resolved):
        print(f"Applied: retained all candidates; marked {len(suspects)} suspected pairs and {len(resolved)} fragments")


if __name__ == "__main__":
    main()
