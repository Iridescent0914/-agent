"""Retire old label group plans while preserving their paid-call audit.

Revision ID: 0031
Revises: 0030
"""

from __future__ import annotations

import json

from alembic import op
import sqlalchemy as sa


revision = "0031"
down_revision = "0030"
branch_labels = None
depends_on = None

LABEL_VERSION = "s3-label-suggestions-v2"


def upgrade() -> None:
    connection = op.get_bind()
    rows = connection.execute(sa.text(
        "SELECT id, checkpoint FROM question_import_batches "
        "WHERE checkpoint #> '{s3,labels}' IS NOT NULL FOR UPDATE"
    )).all()
    for batch_id, checkpoint in rows:
        s3 = checkpoint.get("s3") or {}
        state = s3.get("labels")
        if not isinstance(state, dict) or state.get("version") == LABEL_VERSION:
            continue
        groups = state.pop("groups", [])
        if groups:
            s3.setdefault("label_group_audit", []).append({
                "version": state.get("version"), "groups": groups,
                "calls": state.get("calls"), "estimated_cny": state.get("estimated_cny"),
            })
            state["skipped_item_ids"] = sorted(set(state.get("skipped_item_ids") or []) |
                                                  {item_id for group in groups if isinstance(group, dict)
                                                   for item_id in group.get("skipped_item_ids") or []})
        state["groups"] = []
        state["version"] = LABEL_VERSION
        if state.get("status") != "completed" and not state.get("cost_unknown"):
            state.update({
                "status": "failed",
                "error_code": "S3_LABEL_REPLAN_REQUIRED",
                "error_message": "标签分组规则已更新；重试时将按每组最多 12 题重新处理整批候选题，已成功的题目也会再次调用模型并产生费用",
            })
            state.pop("started_at", None)
        connection.execute(sa.text(
            "UPDATE question_import_batches SET checkpoint = CAST(:checkpoint AS jsonb) "
            "WHERE id = :id"
        ), {"id": batch_id, "checkpoint": json.dumps(checkpoint, ensure_ascii=False)})


def downgrade() -> None:
    # The previous paid calls remain in label_group_audit; active plans are not restored.
    pass
