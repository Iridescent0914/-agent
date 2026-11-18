"""Stage six durable suggestions, calls and per-unit quality feedback.

Revision ID: 0032
Revises: 0031
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

revision = "0032"
down_revision = "0031"
branch_labels = None
depends_on = None


def _id():
    return sa.Column("id", pg.UUID(as_uuid=True), primary_key=True)


def _fk(name, target, nullable=False):
    return sa.Column(name, pg.UUID(as_uuid=True), sa.ForeignKey(target, ondelete="RESTRICT"), nullable=nullable)


def upgrade():
    op.create_table("assessment_inputs", _id(),
        _fk("paper_instance_item_id", "paper_instance_items.id"),
        sa.Column("item_id", sa.String(160), nullable=False),
        sa.Column("revision_no", sa.Integer(), nullable=False),
        sa.Column("snapshot", pg.JSONB(), nullable=False),
        sa.UniqueConstraint("paper_instance_item_id", "item_id", name="uq_assessment_input_unit"),
        sa.CheckConstraint("revision_no > 0", name="ck_assessment_input_revision"))
    op.create_table("assessment_tasks", _id(),
        _fk("input_id", "assessment_inputs.id"),
        sa.Column("input_revision", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("dedup_key", sa.String(64), nullable=False, unique=True),
        sa.Column("snapshot", pg.JSONB(), nullable=False),
        sa.Column("request_payload", pg.JSONB(), nullable=False),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("model", sa.String(160), nullable=False),
        sa.Column("prompt_version", sa.String(80), nullable=False),
        sa.Column("policy_version", sa.String(80), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("timeout_seconds", sa.Integer(), nullable=False),
        sa.Column("lease_seconds", sa.Integer(), nullable=False),
        sa.Column("lease_token", pg.UUID(as_uuid=True)),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("result", pg.JSONB()),
        sa.Column("error_code", sa.String(80)),
        _fk("rerun_of", "assessment_tasks.id", nullable=True),
        sa.Column("rerun_reason", sa.Text()),
        sa.Column("action_history", pg.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("status IN ('queued','running','succeeded','failed','stale','cancelled')", name="ck_assessment_task_status"),
        sa.CheckConstraint("attempt_count >= 0 AND max_attempts > 0 AND attempt_count <= max_attempts", name="ck_assessment_task_attempts"))
    op.create_index("ix_assessment_tasks_queue", "assessment_tasks", ["status", "created_at"])
    op.create_table("assessment_calls", _id(),
        _fk("task_id", "assessment_tasks.id"),
        sa.Column("call_no", sa.Integer(), nullable=False),
        sa.Column("lease_token", pg.UUID(as_uuid=True), nullable=False, unique=True),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("raw_response", sa.Text()),
        sa.Column("parsed_response", pg.JSONB()),
        sa.Column("error_code", sa.String(80)),
        sa.Column("usage", pg.JSONB(), nullable=False),
        sa.Column("late_response", sa.Boolean(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("task_id", "call_no", name="uq_assessment_call_no"))
    op.create_table("model_quality_feedback", _id(),
        sa.Column("target_key", sa.String(400), nullable=False, unique=True),
        _fk("task_id", "assessment_tasks.id", nullable=True),
        sa.Column("recognition_item_id", pg.UUID(as_uuid=True)),
        _fk("paper_instance_item_id", "paper_instance_items.id"),
        sa.Column("item_id", sa.String(160), nullable=False),
        sa.Column("revision_no", sa.Integer(), nullable=False),
        sa.Column("evidence", pg.JSONB(), nullable=False),
        sa.Column("current", pg.JSONB(), nullable=False),
        sa.CheckConstraint("(task_id IS NOT NULL AND recognition_item_id IS NULL) OR (task_id IS NULL AND recognition_item_id IS NOT NULL)", name="ck_quality_feedback_target"),
        sa.CheckConstraint("revision_no > 0", name="ck_quality_feedback_revision"))
    op.create_table("model_quality_feedback_revisions", _id(),
        _fk("feedback_id", "model_quality_feedback.id"),
        sa.Column("revision_no", sa.Integer(), nullable=False),
        sa.Column("data", pg.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("feedback_id", "revision_no", name="uq_quality_feedback_revision"))


def downgrade():
    for table in ("model_quality_feedback_revisions", "model_quality_feedback", "assessment_calls", "assessment_tasks", "assessment_inputs"):
        op.drop_table(table)
