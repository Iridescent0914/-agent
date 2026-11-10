"""Make stored question competencies follow the main formal KP.

Revision ID: 0028
Revises: 0027
"""

from __future__ import annotations

import json
from pathlib import Path

from alembic import op
import sqlalchemy as sa


revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    catalog_path = Path(__file__).resolve().parents[3] / "data" / "knowledge_graph_catalog.json"
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    if catalog.get("schema_version") != "knowledge-graph-catalog-v1":
        raise RuntimeError("Unsupported formal knowledge graph catalog")
    allowed = set(catalog["formal_question_labels"]["core_literacies"])
    by_code = {}
    for chapter in catalog["chapters"]:
        for point in chapter["knowledge_points"]:
            code = point["code"]
            if code is None:
                continue
            competencies = point["core_competencies"]
            if not competencies or len(competencies) != len(set(competencies)) or not set(competencies) <= allowed:
                raise RuntimeError(f"KP competencies need a formal question-label mapping: {code}")
            by_code[code] = "、".join(competencies)

    connection = op.get_bind()
    for point_id, code in connection.execute(sa.text(
        "SELECT id, code FROM knowledge_points FOR UPDATE"
    )).all():
        if code not in by_code:
            raise RuntimeError(f"Database contains a KP outside the formal catalog: {code}")
        connection.execute(sa.text(
            "UPDATE questions SET core_literacy = :label "
            "WHERE main_knowledge_point_id = :id AND core_literacy IS DISTINCT FROM :label"
        ), {"label": by_code[code], "id": point_id})


def downgrade() -> None:
    # Independent historical question labels cannot be reconstructed.
    pass
