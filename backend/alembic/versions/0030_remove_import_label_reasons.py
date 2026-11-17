"""Remove obsolete rationale fields from stored import label suggestions.

Revision ID: 0030
Revises: 0029
"""

from __future__ import annotations

import json
from typing import Any

from alembic import op
import sqlalchemy as sa


revision = "0030"
down_revision = "0029"
branch_labels = None
depends_on = None


def _remove_reason_keys(value: Any) -> bool:
    changed = False
    if isinstance(value, dict):
        if "reason" in value:
            del value["reason"]
            changed = True
        for child in value.values():
            changed = _remove_reason_keys(child) or changed
    elif isinstance(value, list):
        for child in value:
            changed = _remove_reason_keys(child) or changed
    return changed


def _redact_raw_response(raw: str) -> str | None:
    """Keep a readable response audit without retaining old label rationales."""
    try:
        response = json.loads(raw)
        for choice in response.get("choices", []):
            message = choice.get("message") or {}
            content = message.get("content")
            if isinstance(content, str):
                parsed = json.loads(content)
                _remove_reason_keys(parsed)
                message["content"] = json.dumps(parsed, ensure_ascii=False)
        _remove_reason_keys(response)
        return json.dumps(response, ensure_ascii=False)
    except (AttributeError, TypeError, ValueError):
        # A truncated or malformed excerpt cannot be safely redacted.
        return None


def upgrade() -> None:
    connection = op.get_bind()
    connection.execute(sa.text(
        "UPDATE question_import_items "
        "SET payload = payload #- '{label_suggestion,reason}' "
        "WHERE payload #> '{label_suggestion,reason}' IS NOT NULL"
    ))
    rows = connection.execute(sa.text(
        "SELECT id, checkpoint FROM question_import_batches "
        "WHERE checkpoint #> '{s3,labels}' IS NOT NULL FOR UPDATE"
    )).all()
    for batch_id, checkpoint in rows:
        labels = ((checkpoint.get("s3") or {}).get("labels"))
        if not isinstance(labels, dict):
            continue
        changed = _remove_reason_keys(labels)
        for group in labels.get("groups") or []:
            if not isinstance(group, dict) or "raw_response_excerpt" not in group:
                continue
            raw = group["raw_response_excerpt"]
            redacted = _redact_raw_response(raw) if isinstance(raw, str) else None
            if redacted is None:
                group.pop("raw_response_excerpt", None)
                group.pop("raw_response_truncated", None)
            else:
                group["raw_response_excerpt"] = redacted
            changed = True
        if changed:
            connection.execute(sa.text(
                "UPDATE question_import_batches SET checkpoint = CAST(:checkpoint AS jsonb) "
                "WHERE id = :id"
            ), {"id": batch_id, "checkpoint": json.dumps(checkpoint, ensure_ascii=False)})


def downgrade() -> None:
    # Deleted rationale text cannot be reconstructed.
    pass
