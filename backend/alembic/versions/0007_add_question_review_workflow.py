"""add the teacher review workflow for question answers"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "questions",
        sa.Column("review_revision", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column(
        "questions",
        sa.Column(
            "review_history",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.add_column("questions", sa.Column("reviewed_by", sa.String(80), nullable=True))
    op.add_column(
        "questions",
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_check_constraint(
        "ck_questions_review_revision",
        "questions",
        "review_revision >= 1",
    )


def downgrade() -> None:
    op.drop_constraint("ck_questions_review_revision", "questions", type_="check")
    op.drop_column("questions", "reviewed_at")
    op.drop_column("questions", "reviewed_by")
    op.drop_column("questions", "review_history")
    op.drop_column("questions", "review_revision")
