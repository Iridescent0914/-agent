"""add draft deletion and published paper invalidation lifecycle"""

from alembic import op
import sqlalchemy as sa


revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("paper_instances", sa.Column("invalidated_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("paper_instances", sa.Column("invalidation_type", sa.String(24), nullable=True))
    op.add_column("paper_instances", sa.Column("invalidation_reason", sa.Text(), nullable=True))
    op.add_column("paper_instances", sa.Column("invalidated_by", sa.String(80), nullable=True))
    op.add_column("paper_instances", sa.Column("superseded_by_instance_id", sa.UUID(), nullable=True))
    op.create_foreign_key(
        "fk_paper_superseded_by", "paper_instances", "paper_instances",
        ["superseded_by_instance_id"], ["id"], ondelete="RESTRICT",
    )
    op.create_unique_constraint(
        "uq_paper_superseded_by_instance", "paper_instances", ["superseded_by_instance_id"]
    )
    op.create_check_constraint(
        "ck_paper_invalidation_complete",
        "paper_instances",
        "(invalidated_at IS NULL AND invalidation_type IS NULL "
        "AND invalidation_reason IS NULL AND invalidated_by IS NULL "
        "AND superseded_by_instance_id IS NULL) OR "
        "(status = 'published' AND invalidated_at IS NOT NULL "
        "AND invalidation_type IN ('withdrawn', 'superseded') "
        "AND length(btrim(invalidation_reason)) > 0 "
        "AND length(btrim(invalidated_by)) > 0)",
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION reject_published_paper_mutation()
        RETURNS trigger AS $$
        BEGIN
            IF OLD.status = 'published' THEN
                IF TG_OP = 'DELETE' THEN
                    RAISE EXCEPTION 'published paper instance % is immutable', OLD.id;
                END IF;
                IF NEW.id IS DISTINCT FROM OLD.id
                   OR NEW.assignment_id IS DISTINCT FROM OLD.assignment_id
                   OR NEW.student_id IS DISTINCT FROM OLD.student_id
                   OR NEW.version_no IS DISTINCT FROM OLD.version_no
                   OR NEW.status IS DISTINCT FROM OLD.status
                   OR NEW.instance_code IS DISTINCT FROM OLD.instance_code
                   OR NEW.supersedes_instance_id IS DISTINCT FROM OLD.supersedes_instance_id
                   OR NEW.recommendation_audit IS DISTINCT FROM OLD.recommendation_audit
                   OR NEW.created_at IS DISTINCT FROM OLD.created_at
                   OR NEW.published_at IS DISTINCT FROM OLD.published_at THEN
                    RAISE EXCEPTION 'published paper instance % content is immutable', OLD.id;
                END IF;
                IF OLD.invalidated_at IS NOT NULL AND (
                    NEW.invalidated_at IS DISTINCT FROM OLD.invalidated_at
                    OR NEW.invalidation_type IS DISTINCT FROM OLD.invalidation_type
                    OR NEW.invalidation_reason IS DISTINCT FROM OLD.invalidation_reason
                    OR NEW.invalidated_by IS DISTINCT FROM OLD.invalidated_by
                ) THEN
                    RAISE EXCEPTION 'paper instance % invalidation is immutable', OLD.id;
                END IF;
                IF OLD.superseded_by_instance_id IS NOT NULL
                   AND NEW.superseded_by_instance_id IS DISTINCT FROM OLD.superseded_by_instance_id THEN
                    RAISE EXCEPTION 'paper instance % superseding version is immutable', OLD.id;
                END IF;
            END IF;
            RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    # Legacy M3 data allowed more than one published version. Preserve every
    # snapshot and instance code, but invalidate all except the newest version
    # in each Assignment x Student chain before enforcing uniqueness.
    op.execute(
        """
        WITH ordered AS (
            SELECT
                id,
                version_no,
                LEAD(id) OVER chain AS next_id,
                LEAD(version_no) OVER chain AS next_version_no,
                LEAD(published_at) OVER chain AS next_published_at
            FROM paper_instances
            WHERE status = 'published'
            WINDOW chain AS (
                PARTITION BY assignment_id, student_id
                ORDER BY version_no, published_at, id
            )
        )
        UPDATE paper_instances AS paper
        SET invalidated_at = COALESCE(ordered.next_published_at, now()),
            invalidation_type = 'superseded',
            invalidation_reason = format(
                '版本 v%s 发布后由 0008 迁移自动作废历史版本',
                ordered.next_version_no
            ),
            invalidated_by = 'migration_0008',
            superseded_by_instance_id = ordered.next_id,
            edit_revision = paper.edit_revision + 1,
            edit_history = COALESCE(paper.edit_history, '[]'::jsonb) ||
                jsonb_build_array(jsonb_build_object(
                    'event_id', gen_random_uuid()::text,
                    'type', 'superseded',
                    'at', COALESCE(ordered.next_published_at, now()),
                    'actor', 'migration_0008',
                    'before_revision', paper.edit_revision,
                    'after_revision', paper.edit_revision + 1,
                    'details', jsonb_build_object(
                        'superseded_by_instance_id', ordered.next_id,
                        'superseded_by_version_no', ordered.next_version_no,
                        'source', 'legacy_backfill'
                    )
                ))
        FROM ordered
        WHERE paper.id = ordered.id
          AND ordered.next_id IS NOT NULL;
        """
    )
    op.create_index(
        "uq_active_published_paper_per_student", "paper_instances",
        ["assignment_id", "student_id"], unique=True,
        postgresql_where=sa.text("status = 'published' AND invalidated_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_active_published_paper_per_student", table_name="paper_instances")
    op.drop_constraint("ck_paper_invalidation_complete", "paper_instances", type_="check")
    op.drop_constraint("uq_paper_superseded_by_instance", "paper_instances", type_="unique")
    op.drop_constraint("fk_paper_superseded_by", "paper_instances", type_="foreignkey")
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
        """
    )
    op.drop_column("paper_instances", "superseded_by_instance_id")
    op.drop_column("paper_instances", "invalidated_by")
    op.drop_column("paper_instances", "invalidation_reason")
    op.drop_column("paper_instances", "invalidation_type")
    op.drop_column("paper_instances", "invalidated_at")
