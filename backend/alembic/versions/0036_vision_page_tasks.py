"""Offline page-vision task leases and call evidence.

Revision ID: 0036
Revises: 0035
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

revision = "0036"
down_revision = "0035"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("vision_page_tasks",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("collection_id", pg.UUID(as_uuid=True), sa.ForeignKey("answer_page_collections.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("image_id", pg.UUID(as_uuid=True), sa.ForeignKey("answer_page_images.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("page_no", sa.Integer(), nullable=False),
        sa.Column("binding_sha256", sa.String(64), nullable=False),
        sa.Column("dedup_key", sa.String(64), nullable=False, unique=True),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("snapshot", pg.JSONB(), nullable=False),
        sa.Column("request_payload", pg.JSONB(), nullable=False),
        sa.Column("provider", sa.String(64), nullable=False), sa.Column("model", sa.String(160), nullable=False),
        sa.Column("prompt_version", sa.String(80), nullable=False), sa.Column("policy_version", sa.String(80), nullable=False),
        sa.Column("status", sa.String(24), nullable=False), sa.Column("revision_no", sa.Integer(), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False), sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("timeout_seconds", sa.Integer(), nullable=False), sa.Column("lease_seconds", sa.Integer(), nullable=False),
        sa.Column("lease_token", pg.UUID(as_uuid=True)), sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("result", pg.JSONB()), sa.Column("error_code", sa.String(80)),
        sa.Column("rerun_of", pg.UUID(as_uuid=True), sa.ForeignKey("vision_page_tasks.id", ondelete="RESTRICT")),
        sa.Column("action_history", pg.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("status IN ('queued','running','needs_review','failed','stale','cancelled')", name="ck_vision_page_task_status"),
        sa.CheckConstraint("page_no BETWEEN 1 AND 100 AND generation > 0 AND revision_no > 0 AND attempt_count >= 0 AND max_attempts > 0 AND attempt_count <= max_attempts AND lease_seconds > timeout_seconds AND timeout_seconds > 0", name="ck_vision_page_task_counts"))
    op.create_index("ix_vision_page_tasks_queue", "vision_page_tasks", ["status", "created_at"])
    op.create_index("ix_vision_page_tasks_collection", "vision_page_tasks", ["collection_id", "page_no"])
    op.create_table("vision_page_calls",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("task_id", pg.UUID(as_uuid=True), sa.ForeignKey("vision_page_tasks.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("call_no", sa.Integer(), nullable=False),
        sa.Column("lease_token", pg.UUID(as_uuid=True), nullable=False, unique=True),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("raw_response", sa.Text()), sa.Column("parsed_response", pg.JSONB()), sa.Column("error_code", sa.String(80)),
        sa.Column("usage", pg.JSONB(), nullable=False), sa.Column("late_response", sa.Boolean(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False), sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("task_id", "call_no", name="uq_vision_page_call_no"))
    op.execute("""
        CREATE FUNCTION protect_vision_page_task_input() RETURNS trigger AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN RAISE EXCEPTION 'vision page task history is immutable'; END IF;
            IF (NEW.collection_id, NEW.image_id, NEW.page_no, NEW.binding_sha256, NEW.dedup_key,
                NEW.generation, NEW.snapshot, NEW.request_payload, NEW.provider, NEW.model,
                NEW.prompt_version, NEW.policy_version, NEW.max_attempts, NEW.timeout_seconds,
                NEW.lease_seconds, NEW.rerun_of, NEW.created_at)
               IS DISTINCT FROM
               (OLD.collection_id, OLD.image_id, OLD.page_no, OLD.binding_sha256, OLD.dedup_key,
                OLD.generation, OLD.snapshot, OLD.request_payload, OLD.provider, OLD.model,
                OLD.prompt_version, OLD.policy_version, OLD.max_attempts, OLD.timeout_seconds,
                OLD.lease_seconds, OLD.rerun_of, OLD.created_at)
            THEN RAISE EXCEPTION 'vision page task input is immutable'; END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        CREATE TRIGGER vision_page_task_input_immutable BEFORE UPDATE OR DELETE ON vision_page_tasks
        FOR EACH ROW EXECUTE FUNCTION protect_vision_page_task_input();
    """)


def downgrade():
    op.drop_table("vision_page_calls")
    op.drop_table("vision_page_tasks")
    op.execute("DROP FUNCTION protect_vision_page_task_input()")
