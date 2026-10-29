"""Add replayable student knowledge status labels for history and weekly review.

Revision ID: 0024
Revises: 0023
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "student_knowledge_statuses",
        sa.Column(
            "student_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("students.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "knowledge_point_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("knowledge_points.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("status", sa.String(8), nullable=False),
        sa.Column(
            "source_attempt_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("attempts.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("source_revision_no", sa.Integer(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('yes', 'no', 'weak')",
            name="ck_student_knowledge_status",
        ),
        sa.CheckConstraint(
            "source_revision_no >= 1",
            name="ck_student_knowledge_status_revision",
        ),
    )


def downgrade() -> None:
    op.drop_table("student_knowledge_statuses")
