"""add recognition image purge audit state

Revision ID: 0017
Revises: 0016
"""

import sqlalchemy as sa
from alembic import op


revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "recognition_runs",
        sa.Column("image_purged_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_recognition_runs_retention_pending",
        "recognition_runs",
        ["retention_until"],
        postgresql_where=sa.text("image_purged_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_recognition_runs_retention_pending", table_name="recognition_runs")
    op.drop_column("recognition_runs", "image_purged_at")
