'''allow a withdrawn paper version to be republished'''

from alembic import op


revision = '0009'
down_revision = '0008'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        '''
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
                ) AND NOT (
                    OLD.invalidation_type = 'withdrawn'
                    AND OLD.superseded_by_instance_id IS NULL
                    AND NEW.invalidated_at IS NULL
                    AND NEW.invalidation_type IS NULL
                    AND NEW.invalidation_reason IS NULL
                    AND NEW.invalidated_by IS NULL
                    AND NEW.superseded_by_instance_id IS NULL
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
        '''
    )


def downgrade() -> None:
    op.execute(
        '''
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
        '''
    )
