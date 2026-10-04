"""add M6 recognition workflow

Revision ID: 0013
Revises: 0012
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "recognition_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("paper_instance_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("paper_instances.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("student_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("students.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("submission_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("submissions.id", ondelete="RESTRICT"), nullable=True, unique=True),
        sa.Column("idempotency_key", sa.String(100), nullable=False, unique=True),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("model", sa.String(160), nullable=False),
        sa.Column("prompt_version", sa.String(80), nullable=False),
        sa.Column("image_storage_key", sa.String(500), nullable=False),
        sa.Column("image_sha256", sa.String(64), nullable=False),
        sa.Column("original_filename", sa.String(255)),
        sa.Column("content_type", sa.String(80), nullable=False),
        sa.Column("byte_size", sa.BigInteger(), nullable=False),
        sa.Column("pixel_width", sa.Integer(), nullable=False),
        sa.Column("pixel_height", sa.Integer(), nullable=False),
        sa.Column("raw_response", postgresql.JSONB(astext_type=sa.Text())),
        sa.Column("error_code", sa.String(80)),
        sa.Column("error_message", sa.Text()),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("committed_at", sa.DateTime(timezone=True)),
        sa.Column("retention_until", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("status IN ('uploaded', 'processing', 'ready_for_review', 'failed', 'committed', 'discarded')", name="ck_recognition_run_status"),
        sa.CheckConstraint("byte_size > 0", name="ck_recognition_run_byte_size"),
        sa.CheckConstraint("pixel_width > 0 AND pixel_height > 0", name="ck_recognition_run_dimensions"),
    )
    op.create_index("ix_recognition_runs_paper_status", "recognition_runs", ["paper_instance_id", "status"])
    op.create_table(
        "recognition_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("recognition_run_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("recognition_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("paper_instance_item_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("paper_instance_items.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("order_no", sa.Integer(), nullable=False),
        sa.Column("recognition_status", sa.String(24), nullable=False),
        sa.Column("recognized_answer", postgresql.JSONB(astext_type=sa.Text())),
        sa.Column("confidence", sa.Numeric(5, 4)),
        sa.Column("provider_payload", postgresql.JSONB(astext_type=sa.Text())),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("recognition_run_id", "paper_instance_item_id", name="uq_recognition_run_item"),
        sa.UniqueConstraint("recognition_run_id", "order_no", name="uq_recognition_run_order"),
        sa.CheckConstraint("order_no > 0", name="ck_recognition_item_order"),
        sa.CheckConstraint("recognition_status IN ('readable', 'blank', 'missing', 'illegible', 'ambiguous')", name="ck_recognition_item_status"),
        sa.CheckConstraint("confidence IS NULL OR (confidence BETWEEN 0 AND 1)", name="ck_recognition_item_confidence"),
    )


def downgrade() -> None:
    op.drop_table("recognition_items")
    op.drop_index("ix_recognition_runs_paper_status", table_name="recognition_runs")
    op.drop_table("recognition_runs")
