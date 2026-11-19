"""Whole-paper transcription review and immutable revisions."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

revision = "0037"
down_revision = "0036"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("answer_transcriptions",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("collection_id", pg.UUID(as_uuid=True), sa.ForeignKey("answer_page_collections.id", ondelete="RESTRICT"), nullable=False, unique=True),
        sa.Column("revision_no", sa.Integer(), nullable=False),
        sa.Column("input_sha256", sa.String(64), nullable=False),
        sa.Column("evidence", pg.JSONB(), nullable=False),
        sa.Column("units", pg.JSONB(), nullable=False),
        sa.Column("confirmed_page_nos", pg.JSONB(), nullable=False),
        sa.Column("submission_id", pg.UUID(as_uuid=True), sa.ForeignKey("submissions.id", ondelete="RESTRICT"), unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("revision_no > 0", name="ck_answer_transcription_revision"))
    op.create_table("answer_transcription_revisions",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("transcription_id", pg.UUID(as_uuid=True), sa.ForeignKey("answer_transcriptions.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("revision_no", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(16), nullable=False),
        sa.Column("state", pg.JSONB(), nullable=False),
        sa.Column("reviewer", sa.String(100), nullable=False),
        sa.Column("reason", sa.String(500), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("transcription_id", "revision_no", name="uq_answer_transcription_revision"))
    op.execute("""
        CREATE FUNCTION protect_answer_transcription_history() RETURNS trigger AS $$
        BEGIN RAISE EXCEPTION 'answer transcription history is immutable'; END;
        $$ LANGUAGE plpgsql;
        CREATE TRIGGER answer_transcription_revisions_immutable BEFORE UPDATE OR DELETE ON answer_transcription_revisions
        FOR EACH ROW EXECUTE FUNCTION protect_answer_transcription_history();
        CREATE FUNCTION protect_submitted_transcription() RETURNS trigger AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN RAISE EXCEPTION 'transcriptions cannot be deleted'; END IF;
            IF OLD.submission_id IS NOT NULL OR NEW.collection_id IS DISTINCT FROM OLD.collection_id
            THEN RAISE EXCEPTION 'submitted transcription and ownership are immutable'; END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        CREATE TRIGGER answer_transcription_submitted_immutable BEFORE UPDATE OR DELETE ON answer_transcriptions
        FOR EACH ROW EXECUTE FUNCTION protect_submitted_transcription();
    """)


def downgrade():
    op.drop_table("answer_transcription_revisions")
    op.drop_table("answer_transcriptions")
    op.execute("DROP FUNCTION protect_submitted_transcription()")
    op.execute("DROP FUNCTION protect_answer_transcription_history()")
