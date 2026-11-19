"""Answer-page photo collection, revisions and audit (no recognition).

Revision ID: 0035
Revises: 0034
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

revision = "0035"
down_revision = "0034"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("answer_page_collections",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("artifact_id", pg.UUID(as_uuid=True), sa.ForeignKey("answer_card_artifacts.id", ondelete="RESTRICT"), nullable=False, unique=True),
        sa.Column("revision_no", sa.Integer(), nullable=False),
        sa.Column("effective_pages", pg.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("revision_no >= 0", name="ck_answer_collection_revision"))
    op.create_table("answer_page_images",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("collection_id", pg.UUID(as_uuid=True), sa.ForeignKey("answer_page_collections.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("image_sha256", sa.String(64), nullable=False),
        sa.Column("width", sa.Integer(), nullable=False), sa.Column("height", sa.Integer(), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False), sa.Column("image_data", sa.LargeBinary()),
        sa.Column("retention_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("purged_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("collection_id", "image_sha256", name="uq_answer_page_image_hash"),
        sa.CheckConstraint("width > 0 AND height > 0 AND byte_size > 0", name="ck_answer_page_image_size"))
    op.create_index("ix_answer_page_images_collection_id", "answer_page_images", ["collection_id"])
    op.create_index("ix_answer_page_images_image_sha256", "answer_page_images", ["image_sha256"])
    op.create_table("answer_page_events",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("collection_id", pg.UUID(as_uuid=True), sa.ForeignKey("answer_page_collections.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("revision_no", sa.Integer(), nullable=False), sa.Column("page_no", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(20), nullable=False),
        sa.Column("before_image_id", pg.UUID(as_uuid=True), sa.ForeignKey("answer_page_images.id", ondelete="RESTRICT")),
        sa.Column("after_image_id", pg.UUID(as_uuid=True), sa.ForeignKey("answer_page_images.id", ondelete="RESTRICT")),
        sa.Column("reviewer", sa.String(100), nullable=False), sa.Column("reason", sa.String(500), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("collection_id", "revision_no", name="uq_answer_page_event_revision"),
        sa.CheckConstraint("revision_no > 0 AND page_no BETWEEN 1 AND 100", name="ck_answer_page_event_counts"))
    op.create_index("ix_answer_page_events_collection_id", "answer_page_events", ["collection_id"])
    op.execute("""
        CREATE FUNCTION reject_answer_page_event_mutation() RETURNS trigger AS $$
        BEGIN RAISE EXCEPTION 'answer page events are immutable'; END;
        $$ LANGUAGE plpgsql;
        CREATE TRIGGER answer_page_events_immutable BEFORE UPDATE OR DELETE ON answer_page_events
        FOR EACH ROW EXECUTE FUNCTION reject_answer_page_event_mutation();
    """)


def downgrade():
    op.drop_table("answer_page_events")
    op.execute("DROP FUNCTION reject_answer_page_event_mutation()")
    op.drop_table("answer_page_images")
    op.drop_table("answer_page_collections")
