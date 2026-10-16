"""Add knowledge graph metadata.

Revision ID: 0020
Revises: 0019
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "knowledge_points",
        sa.Column("mastery_level", sa.String(length=16)),
    )
    op.add_column(
        "knowledge_points",
        sa.Column(
            "core_competencies",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.add_column("knowledge_points", sa.Column("curriculum_basis", sa.Text()))
    op.alter_column("knowledge_points", "core_competencies", server_default=None)


def downgrade() -> None:
    op.drop_column("knowledge_points", "curriculum_basis")
    op.drop_column("knowledge_points", "core_competencies")
    op.drop_column("knowledge_points", "mastery_level")
