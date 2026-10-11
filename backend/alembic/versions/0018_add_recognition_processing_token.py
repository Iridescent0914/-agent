"""add recognition processing claim token

Revision ID: 0018
Revises: 0017
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "recognition_runs",
        sa.Column("processing_token", postgresql.UUID(as_uuid=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("recognition_runs", "processing_token")
