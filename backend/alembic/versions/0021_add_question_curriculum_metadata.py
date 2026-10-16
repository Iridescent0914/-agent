"""Add curriculum metadata from the revised school-reviewed question bank.

Revision ID: 0021
Revises: 0020
"""

from alembic import op
import sqlalchemy as sa


revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "questions",
        sa.Column("grade", sa.String(length=80), nullable=False, server_default="未设置"),
    )
    op.add_column(
        "questions",
        sa.Column(
            "core_literacy",
            sa.String(length=255),
            nullable=False,
            server_default="待标注",
        ),
    )
    op.add_column(
        "questions",
        sa.Column("is_required", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.alter_column("questions", "grade", server_default=None)
    op.alter_column("questions", "core_literacy", server_default=None)
    op.alter_column("questions", "is_required", server_default=None)


def downgrade() -> None:
    op.drop_column("questions", "is_required")
    op.drop_column("questions", "core_literacy")
    op.drop_column("questions", "grade")
