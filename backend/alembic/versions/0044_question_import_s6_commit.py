"""Record idempotent S6 commits and prevent duplicate item imports.

Revision ID: 0044
Revises: 0043
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0044"
down_revision = "0043"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "question_import_commits",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("batch_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("question_import_batches.id", ondelete="CASCADE"), nullable=False),
        sa.Column("operation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("request_signature", sa.String(64), nullable=False),
        sa.Column("result", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("batch_id", "operation_id", name="uq_question_import_commit_operation"),
    )
    op.create_index("uq_questions_import_item_once", "questions", ["import_item_id"], unique=True,
                    postgresql_where=sa.text("import_item_id IS NOT NULL"))


def downgrade() -> None:
    op.drop_index("uq_questions_import_item_once", table_name="questions")
    op.drop_table("question_import_commits")
