"""Whole-paper recognition quality labels and immutable history."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

revision = "0042"
down_revision = "0041"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("paper_recognition_reviews",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("collection_id", pg.UUID(as_uuid=True), sa.ForeignKey("answer_page_collections.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("recognition_sha256", sa.String(64), nullable=False),
        sa.Column("revision_no", sa.Integer(), nullable=False),
        sa.Column("evidence", pg.JSONB(), nullable=False),
        sa.Column("current", pg.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("collection_id", "recognition_sha256", name="uq_paper_recognition_review_target"),
        sa.CheckConstraint("revision_no > 0", name="ck_paper_recognition_review_revision"))
    op.create_table("paper_recognition_review_revisions",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("review_id", pg.UUID(as_uuid=True), sa.ForeignKey("paper_recognition_reviews.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("revision_no", sa.Integer(), nullable=False),
        sa.Column("data", pg.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("review_id", "revision_no", name="uq_paper_recognition_review_revision"),
        sa.CheckConstraint("revision_no > 0", name="ck_paper_recognition_review_history_revision"))
    op.execute("""
        CREATE FUNCTION protect_paper_recognition_review_history() RETURNS trigger AS $$
        BEGIN RAISE EXCEPTION 'paper recognition review history is immutable'; END;
        $$ LANGUAGE plpgsql;
        CREATE TRIGGER paper_recognition_review_history_immutable
        BEFORE UPDATE OR DELETE ON paper_recognition_review_revisions
        FOR EACH ROW EXECUTE FUNCTION protect_paper_recognition_review_history();
        CREATE FUNCTION protect_paper_recognition_review_evidence() RETURNS trigger AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN RAISE EXCEPTION 'paper recognition reviews cannot be deleted'; END IF;
            IF NEW.collection_id IS DISTINCT FROM OLD.collection_id
                OR NEW.recognition_sha256 IS DISTINCT FROM OLD.recognition_sha256
                OR NEW.evidence IS DISTINCT FROM OLD.evidence
            THEN RAISE EXCEPTION 'paper recognition review evidence is immutable'; END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        CREATE TRIGGER paper_recognition_review_evidence_immutable
        BEFORE UPDATE OR DELETE ON paper_recognition_reviews
        FOR EACH ROW EXECUTE FUNCTION protect_paper_recognition_review_evidence();
    """)


def downgrade():
    op.drop_table("paper_recognition_review_revisions")
    op.drop_table("paper_recognition_reviews")
    op.execute("DROP FUNCTION protect_paper_recognition_review_history()")
    op.execute("DROP FUNCTION protect_paper_recognition_review_evidence()")
