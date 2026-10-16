"""Sync mutable workflow data to the revised knowledge-point taxonomy.

Revision ID: 0022
Revises: 0021

Published paper snapshots are intentionally left unchanged.  They are audit
records; services resolve their question IDs to the current KP when computing
profiles or rendering current teaching views.
"""

from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
import json

from alembic import op
import sqlalchemy as sa


revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


# These legacy scopes had no selected paper item, so they cannot be inferred
# from an immutable question snapshot.  Their old labels map to the formal KPs
# shown below (the second legacy label combined two concepts).
LEGACY_SCOPE_FALLBACKS = {
    "math.g6.rational.exam.local.45115cda57": [
        "math.g6.rational.tracking.03_number_line",
    ],
    "math.g6.rational.exam.local.8c72646615": [
        "math.g6.rational.tracking.05_absolute_value",
        "math.g6.rational.tracking.06_comparison",
    ],
}


def _remap_codes(values: object, mapping: dict[str, list[str]]) -> object:
    if not isinstance(values, list):
        return values
    output: list[str] = []
    for value in values:
        replacements = mapping.get(value, [value]) if isinstance(value, str) else []
        for replacement in replacements:
            if replacement not in output:
                output.append(replacement)
    return output


def _remap_parameters(payload: object, mapping: dict[str, list[str]]) -> object:
    if not isinstance(payload, dict):
        return payload
    value = deepcopy(payload)
    new_codes = _remap_codes(value.get("new_exam_point_codes"), mapping)
    review_codes = _remap_codes(value.get("review_exam_point_codes"), mapping)
    if isinstance(new_codes, list):
        value["new_exam_point_codes"] = new_codes
    if isinstance(review_codes, list):
        # The request contract requires new/review scopes to be disjoint.
        value["review_exam_point_codes"] = [
            code for code in review_codes if code not in set(new_codes or [])
        ]
    return value


def _current_item_audit(
    payload: object,
    current: dict[str, str] | None,
) -> object:
    if not isinstance(payload, dict) or current is None:
        return payload
    value = deepcopy(payload)
    value["exam_point_code"] = current["id"]
    value["exam_point_name"] = current["name"]
    if isinstance(value.get("reason_text"), str):
        value["reason_text"] = value["reason_text"].replace("考点", "知识点")
    return value


def _remap_recommendation_audit(
    payload: object,
    mapping: dict[str, list[str]],
    current_by_question: dict[str, dict[str, str]],
) -> object:
    if not isinstance(payload, dict):
        return payload
    value = deepcopy(payload)
    value["parameters"] = _remap_parameters(value.get("parameters"), mapping)
    items = value.get("items")
    if isinstance(items, list):
        value["items"] = [
            _current_item_audit(item, current_by_question.get(str(item.get("question_id"))))
            if isinstance(item, dict)
            else item
            for item in items
        ]
        selected_per: dict[str, int] = defaultdict(int)
        for item in value["items"]:
            if isinstance(item, dict) and isinstance(item.get("exam_point_code"), str):
                selected_per[item["exam_point_code"]] += 1
        value["selected_per_exam_point"] = dict(selected_per)
    diagnostics = value.get("diagnostics")
    if isinstance(diagnostics, list):
        for diagnostic in diagnostics:
            if not isinstance(diagnostic, dict):
                continue
            diagnostic["exam_point_codes"] = _remap_codes(
                diagnostic.get("exam_point_codes"), mapping
            )
            diagnostic["capped_exam_point_codes"] = _remap_codes(
                diagnostic.get("capped_exam_point_codes"), mapping
            )
    return value


def _dump(value: object) -> str:
    return json.dumps(value, ensure_ascii=False)


def upgrade() -> None:
    connection = op.get_bind()
    rows = connection.execute(sa.text("""
        SELECT pii.question_id::text AS question_id,
               pii.exam_point_snapshot->'main'->>'id' AS old_code,
               ep.code AS current_code,
               ep.name AS current_name,
               kp.sort_order AS kp_order,
               ep.sort_order AS exam_order
        FROM paper_instance_items pii
        JOIN question_exam_points qep
          ON qep.question_id = pii.question_id AND qep.relation_type = 'main'
        JOIN exam_points ep ON ep.id = qep.exam_point_id
        JOIN knowledge_points kp ON kp.id = ep.knowledge_point_id
        WHERE pii.exam_point_snapshot->'main'->>'id' LIKE '%.exam.local.%'
        ORDER BY kp.sort_order, ep.sort_order, ep.code
    """)).mappings().all()

    mapping_sets: dict[str, list[str]] = defaultdict(list)
    current_by_question: dict[str, dict[str, str]] = {}
    for row in rows:
        current = {"id": row["current_code"], "name": row["current_name"]}
        current_by_question[row["question_id"]] = current
        if row["current_code"] not in mapping_sets[row["old_code"]]:
            mapping_sets[row["old_code"]].append(row["current_code"])
    mapping = {**LEGACY_SCOPE_FALLBACKS, **dict(mapping_sets)}

    assignments = connection.execute(sa.text("""
        SELECT id::text AS id, teaching_scope, generation_config
        FROM assignments
        WHERE teaching_scope::text LIKE '%.exam.local.%'
           OR generation_config::text LIKE '%.exam.local.%'
    """)).mappings().all()
    for row in assignments:
        scope = _remap_parameters(row["teaching_scope"], mapping)
        config = deepcopy(row["generation_config"] or {})
        if isinstance(config, dict):
            config["recommendation_parameters"] = _remap_parameters(
                config.get("recommendation_parameters"), mapping
            )
        connection.execute(
            sa.text("""
                UPDATE assignments
                SET teaching_scope = CAST(:scope AS jsonb),
                    generation_config = CAST(:config AS jsonb)
                WHERE id = CAST(:id AS uuid)
            """),
            {"id": row["id"], "scope": _dump(scope), "config": _dump(config)},
        )

    draft_items = connection.execute(sa.text("""
        SELECT pii.id::text AS id, pii.question_id::text AS question_id,
               pii.exam_point_snapshot, pii.selection_audit
        FROM paper_instance_items pii
        JOIN paper_instances pi ON pi.id = pii.paper_instance_id
        WHERE pi.status = 'draft'
    """)).mappings().all()
    for row in draft_items:
        current = current_by_question.get(row["question_id"])
        if current is None:
            current_row = connection.execute(sa.text("""
                SELECT ep.code, ep.name
                FROM question_exam_points qep
                JOIN exam_points ep ON ep.id = qep.exam_point_id
                WHERE qep.question_id = CAST(:question_id AS uuid)
                  AND qep.relation_type = 'main'
            """), {"question_id": row["question_id"]}).mappings().first()
            if current_row is None:
                continue
            current = {"id": current_row["code"], "name": current_row["name"]}
            current_by_question[row["question_id"]] = current
        snapshot = deepcopy(row["exam_point_snapshot"] or {})
        snapshot["main"] = current
        audit = _current_item_audit(row["selection_audit"], current)
        connection.execute(
            sa.text("""
                UPDATE paper_instance_items
                SET exam_point_snapshot = CAST(:snapshot AS jsonb),
                    selection_audit = CAST(:audit AS jsonb)
                WHERE id = CAST(:id AS uuid)
            """),
            {"id": row["id"], "snapshot": _dump(snapshot), "audit": _dump(audit)},
        )

    drafts = connection.execute(sa.text("""
        SELECT id::text AS id, recommendation_audit
        FROM paper_instances
        WHERE status = 'draft' AND recommendation_audit IS NOT NULL
    """)).mappings().all()
    for row in drafts:
        audit = _remap_recommendation_audit(
            row["recommendation_audit"], mapping, current_by_question
        )
        connection.execute(
            sa.text("""
                UPDATE paper_instances
                SET recommendation_audit = CAST(:audit AS jsonb)
                WHERE id = CAST(:id AS uuid)
            """),
            {"id": row["id"], "audit": _dump(audit)},
        )


def downgrade() -> None:
    # Old source taxonomy is deliberately not reconstructed. Published audit
    # snapshots were never modified and remain the authoritative history.
    pass
