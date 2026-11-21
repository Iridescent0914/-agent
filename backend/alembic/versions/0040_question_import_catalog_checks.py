"""Persist S4 catalog evidence and decisions.

Revision ID: 0040
Revises: 0039
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

revision = "0040"
down_revision = "0039"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "question_import_catalog_checks",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("batch_id", pg.UUID(as_uuid=True), sa.ForeignKey("question_import_batches.id", ondelete="CASCADE"), nullable=False),
        sa.Column("item_id", pg.UUID(as_uuid=True), sa.ForeignKey("question_import_items.id", ondelete="CASCADE"), nullable=False),
        sa.Column("check_no", sa.Integer(), nullable=False),
        sa.Column("candidate_revision", sa.Integer(), nullable=False),
        sa.Column("candidate_signature", sa.String(64), nullable=False),
        sa.Column("exact_version", sa.String(40), nullable=False),
        sa.Column("near_version", sa.String(40), nullable=False),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("item_id", "check_no", name="uq_import_catalog_item_check_no"),
    )
    op.create_index("ix_import_catalog_checks_batch_item", "question_import_catalog_checks", ["batch_id", "item_id", "check_no"])
    op.create_table(
        "question_import_catalog_matches",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("check_id", pg.UUID(as_uuid=True), sa.ForeignKey("question_import_catalog_checks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("question_id", pg.UUID(as_uuid=True), sa.ForeignKey("questions.id", ondelete="SET NULL")),
        sa.Column("original_qid", sa.String(180), nullable=False),
        sa.Column("catalog_review_revision", sa.Integer(), nullable=False),
        sa.Column("catalog_signature", sa.String(64)),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("similarity", sa.Float()),
        sa.Column("evidence", pg.JSONB(), nullable=False),
        sa.Column("decision", sa.String(24), nullable=False, server_default="pending"),
        sa.Column("reason", sa.Text()),
        sa.Column("decided_at", sa.DateTime(timezone=True)),
        sa.Column("decision_scope", sa.String(80)),
        sa.UniqueConstraint("check_id", "question_id", name="uq_import_catalog_check_question"),
    )
    op.create_index("ix_import_catalog_matches_check", "question_import_catalog_matches", ["check_id"])


def downgrade() -> None:
    op.drop_index("ix_import_catalog_matches_check", table_name="question_import_catalog_matches")
    op.drop_table("question_import_catalog_matches")
    op.drop_index("ix_import_catalog_checks_batch_item", table_name="question_import_catalog_checks")
    op.drop_table("question_import_catalog_checks")
