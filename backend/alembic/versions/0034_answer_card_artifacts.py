"""Immutable answer-card PDFs and frozen page manifests.

Revision ID: 0034
Revises: 0033
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

revision = "0034"
down_revision = "0033"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "answer_card_artifacts",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("paper_instance_id", pg.UUID(as_uuid=True), sa.ForeignKey("paper_instances.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("revision_no", sa.Integer(), nullable=False),
        sa.Column("artifact_code", sa.String(32), nullable=False, unique=True),
        sa.Column("dedup_key", sa.String(64), nullable=False, unique=True),
        sa.Column("source_sha256", sa.String(64), nullable=False),
        sa.Column("pdf_sha256", sa.String(64), nullable=False),
        sa.Column("manifest_sha256", sa.String(64), nullable=False),
        sa.Column("layout_version", sa.String(64), nullable=False),
        sa.Column("renderer_version", sa.String(100), nullable=False),
        sa.Column("total_pages", sa.Integer(), nullable=False),
        sa.Column("manifest", pg.JSONB(), nullable=False),
        sa.Column("pdf_data", sa.LargeBinary(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("paper_instance_id", "revision_no", name="uq_answer_card_revision"),
        sa.CheckConstraint("revision_no > 0 AND total_pages > 0 AND total_pages <= 100", name="ck_answer_card_counts"),
    )
    op.execute("""
        CREATE FUNCTION reject_answer_card_artifact_mutation() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'answer card artifacts are immutable';
        END;
        $$ LANGUAGE plpgsql;
        CREATE TRIGGER answer_card_artifacts_immutable
        BEFORE UPDATE OR DELETE ON answer_card_artifacts
        FOR EACH ROW EXECUTE FUNCTION reject_answer_card_artifact_mutation();
    """)


def downgrade():
    op.drop_table("answer_card_artifacts")
    op.execute("DROP FUNCTION reject_answer_card_artifact_mutation()")
