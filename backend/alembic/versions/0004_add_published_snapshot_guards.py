"""protect published paper snapshots"""

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION reject_published_paper_mutation()
        RETURNS trigger AS $$
        BEGIN
            IF OLD.status = 'published' THEN
                RAISE EXCEPTION 'published paper instance % is immutable', OLD.id;
            END IF;
            RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
        END;
        $$ LANGUAGE plpgsql;

        CREATE TRIGGER paper_instance_published_guard
        BEFORE UPDATE OR DELETE ON paper_instances
        FOR EACH ROW EXECUTE FUNCTION reject_published_paper_mutation();
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION reject_published_paper_item_mutation()
        RETURNS trigger AS $$
        DECLARE
            target_paper_id uuid;
            target_status varchar;
        BEGIN
            target_paper_id := CASE WHEN TG_OP = 'DELETE' THEN OLD.paper_instance_id ELSE NEW.paper_instance_id END;
            SELECT status INTO target_status FROM paper_instances WHERE id = target_paper_id;
            IF target_status = 'published' THEN
                RAISE EXCEPTION 'items of published paper instance % are immutable', target_paper_id;
            END IF;
            RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
        END;
        $$ LANGUAGE plpgsql;

        CREATE TRIGGER paper_item_published_guard
        BEFORE INSERT OR UPDATE OR DELETE ON paper_instance_items
        FOR EACH ROW EXECUTE FUNCTION reject_published_paper_item_mutation();
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS paper_item_published_guard ON paper_instance_items")
    op.execute("DROP FUNCTION IF EXISTS reject_published_paper_item_mutation()")
    op.execute("DROP TRIGGER IF EXISTS paper_instance_published_guard ON paper_instances")
    op.execute("DROP FUNCTION IF EXISTS reject_published_paper_mutation()")
