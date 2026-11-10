"""Require formal graph KPs and derive stored question labels from each KP.

Revision ID: 0027
Revises: 0026

The versioned data/knowledge_graph_catalog.json is the runtime label source.
Unknown KPs are a migration error: deleting or remapping referenced KPs would
change existing student and paper history and requires an explicit mapping.
"""

from __future__ import annotations

import json
from pathlib import Path

from alembic import op
import sqlalchemy as sa


revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None


def _formal_kps() -> dict[str, dict]:
    path = Path(__file__).resolve().parents[3] / "data" / "knowledge_graph_catalog.json"
    catalog = json.loads(path.read_text(encoding="utf-8"))
    if catalog.get("schema_version") != "knowledge-graph-catalog-v1":
        raise RuntimeError("Unsupported formal knowledge graph catalog")
    result = {}
    for chapter in catalog["chapters"]:
        for point in chapter["knowledge_points"]:
            code = point["code"]
            if code is None:
                continue
            if code in result:
                raise RuntimeError(f"Duplicate formal KP code: {code}")
            result[code] = {
                "name": point["name"],
                "chapter_code": chapter["code"],
                "chapter_name": chapter["name"],
                "grade": chapter["grade"],
                "section": point["section"],
                "sort_order": point["order"],
                "mastery_level": point["mastery_level"],
                "core_competencies": point["core_competencies"],
                "curriculum_basis": point["curriculum_basis"],
            }
    return result


def upgrade() -> None:
    canonical = _formal_kps()
    connection = op.get_bind()
    points = connection.execute(sa.text(
        "SELECT id, code FROM knowledge_points FOR UPDATE"
    )).all()
    unknown = sorted(code for _id, code in points if code not in canonical)
    if unknown:
        raise RuntimeError(
            "Database contains KPs outside the formal HTML-derived catalog: "
            + ", ".join(unknown)
        )
    for point_id, code in points:
        values = canonical[code]
        connection.execute(sa.text(
            "UPDATE knowledge_points SET name = :name, chapter_code = :chapter_code, "
            "chapter_name = :chapter_name, section = :section, sort_order = :sort_order, "
            "mastery_level = :mastery_level, core_competencies = CAST(:core_competencies AS jsonb), "
            "curriculum_basis = :curriculum_basis WHERE id = :id"
        ), {**values, "core_competencies": json.dumps(values["core_competencies"], ensure_ascii=False), "id": point_id})
        connection.execute(sa.text(
            "UPDATE questions SET chapter_code = :chapter_code, chapter_name = :chapter_name, "
            "grade = :grade, section = :section WHERE main_knowledge_point_id = :id "
            "AND (chapter_code IS DISTINCT FROM :chapter_code "
            "OR chapter_name IS DISTINCT FROM :chapter_name "
            "OR grade IS DISTINCT FROM :grade OR section IS DISTINCT FROM :section)"
        ), {
            "chapter_code": values["chapter_code"],
            "chapter_name": values["chapter_name"],
            "grade": values["grade"],
            "section": values["section"],
            "id": point_id,
        })


def downgrade() -> None:
    # Previous independent values cannot be reconstructed reliably.
    pass
