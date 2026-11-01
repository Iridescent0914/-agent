"""Add replayable dynamic literacy score materialization.

Revision ID: 0025
Revises: 0024
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "student_literacy_scores",
        sa.Column(
            "student_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("students.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("literacy_name", sa.String(40), primary_key=True),
        sa.Column("score", sa.Numeric(7, 4), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("last_practiced_at", sa.DateTime(timezone=True)),
        sa.Column("score_as_of_date", sa.Date(), nullable=False),
        sa.Column("rule_version", sa.String(64), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "score BETWEEN 0 AND 100",
            name="ck_literacy_score_range",
        ),
        sa.CheckConstraint(
            "attempt_count >= 0",
            name="ck_literacy_attempt_count_nonnegative",
        ),
    )


def downgrade() -> None:
    op.drop_table("student_literacy_scores")
