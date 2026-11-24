"""Persist S5 teacher review and issue decisions.

Revision ID: 0041
Revises: 0040
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

revision = "0041"
down_revision = "0040"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "question_import_audit_events",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("batch_id", pg.UUID(as_uuid=True), sa.ForeignKey("question_import_batches.id", ondelete="CASCADE"), nullable=False),
        sa.Column("item_id", pg.UUID(as_uuid=True), sa.ForeignKey("question_import_items.id", ondelete="CASCADE")),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("decision", sa.String(32), nullable=False),
        sa.Column("candidate_revision", sa.Integer()),
        sa.Column("signature", sa.String(64)),
        sa.Column("issue_key", sa.String(240)),
        sa.Column("reason", sa.Text()),
        sa.Column("detail", pg.JSONB(), nullable=False),
        sa.Column("actor_scope", sa.String(80), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_import_audit_batch_item", "question_import_audit_events", ["batch_id", "item_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_import_audit_batch_item", table_name="question_import_audit_events")
    op.drop_table("question_import_audit_events")
