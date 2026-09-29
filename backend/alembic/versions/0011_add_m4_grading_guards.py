'''add M4 grading state, idempotency fingerprint and database guards'''

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = '0011'
down_revision = '0010'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'submissions',
        sa.Column('request_fingerprint', sa.String(64), nullable=True),
    )
    op.add_column(
        'attempts',
        sa.Column(
            'grading_details', postgresql.JSONB(), nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )

    op.drop_constraint('ck_needs_review_unknown', 'attempts', type_='check')
    op.create_check_constraint(
        'ck_attempt_result_complete',
        'attempts',
        "(grade_status = 'needs_review' AND is_correct IS NULL) OR "
        "(grade_status IN ('auto_confirmed', 'teacher_confirmed') AND is_correct IS NOT NULL)",
    )
    op.create_check_constraint(
        'ck_attempt_ocr_confidence_range',
        'attempts',
        'ocr_confidence IS NULL OR (ocr_confidence BETWEEN 0 AND 1)',
    )
    op.create_check_constraint(
        'ck_attempt_manual_override_status',
        'attempts',
        "NOT manual_override OR grade_status IN ('teacher_confirmed', 'needs_review')",
    )

    op.execute(
        '''
        CREATE FUNCTION validate_m4_submission_ownership() RETURNS trigger AS $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM paper_instances p
                WHERE p.id = NEW.paper_instance_id AND p.student_id = NEW.student_id
            ) THEN
                RAISE EXCEPTION 'submission student does not own paper instance';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;

        CREATE TRIGGER trg_validate_m4_submission_ownership
        BEFORE INSERT OR UPDATE OF student_id, paper_instance_id ON submissions
        FOR EACH ROW EXECUTE FUNCTION validate_m4_submission_ownership();
        '''
    )
    op.execute(
        '''
        CREATE FUNCTION validate_m4_attempt_ownership() RETURNS trigger AS $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1
                FROM submissions s
                JOIN paper_instance_items i ON i.id = NEW.paper_instance_item_id
                WHERE s.id = NEW.submission_id
                  AND s.student_id = NEW.student_id
                  AND s.paper_instance_id = i.paper_instance_id
                  AND i.question_id = NEW.question_id
            ) THEN
                RAISE EXCEPTION 'attempt ownership does not match submission and paper item';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;

        CREATE TRIGGER trg_validate_m4_attempt_ownership
        BEFORE INSERT OR UPDATE OF submission_id, student_id, paper_instance_item_id, question_id
        ON attempts
        FOR EACH ROW EXECUTE FUNCTION validate_m4_attempt_ownership();
        '''
    )
    op.execute(
        '''
        CREATE FUNCTION validate_m4_attempt_current_revision() RETURNS trigger AS $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM attempt_revisions r
                WHERE r.attempt_id = NEW.id AND r.revision_no = NEW.revision_no
            ) THEN
                RAISE EXCEPTION 'attempt current revision is missing';
            END IF;
            RETURN NULL;
        END;
        $$ LANGUAGE plpgsql;

        CREATE CONSTRAINT TRIGGER trg_validate_m4_attempt_current_revision
        AFTER INSERT OR UPDATE OF revision_no ON attempts
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION validate_m4_attempt_current_revision();
        '''
    )
    op.execute(
        '''
        CREATE FUNCTION protect_m4_attempt_revision() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'attempt revisions are append-only';
        END;
        $$ LANGUAGE plpgsql;

        CREATE TRIGGER trg_protect_m4_attempt_revision
        BEFORE UPDATE OR DELETE ON attempt_revisions
        FOR EACH ROW EXECUTE FUNCTION protect_m4_attempt_revision();
        '''
    )


def downgrade() -> None:
    op.execute('DROP TRIGGER IF EXISTS trg_protect_m4_attempt_revision ON attempt_revisions')
    op.execute('DROP FUNCTION IF EXISTS protect_m4_attempt_revision()')
    op.execute('DROP TRIGGER IF EXISTS trg_validate_m4_attempt_current_revision ON attempts')
    op.execute('DROP FUNCTION IF EXISTS validate_m4_attempt_current_revision()')
    op.execute('DROP TRIGGER IF EXISTS trg_validate_m4_attempt_ownership ON attempts')
    op.execute('DROP FUNCTION IF EXISTS validate_m4_attempt_ownership()')
    op.execute('DROP TRIGGER IF EXISTS trg_validate_m4_submission_ownership ON submissions')
    op.execute('DROP FUNCTION IF EXISTS validate_m4_submission_ownership()')

    op.drop_constraint('ck_attempt_manual_override_status', 'attempts', type_='check')
    op.drop_constraint('ck_attempt_ocr_confidence_range', 'attempts', type_='check')
    op.drop_constraint('ck_attempt_result_complete', 'attempts', type_='check')
    op.create_check_constraint(
        'ck_needs_review_unknown',
        'attempts',
        "grade_status <> 'needs_review' OR is_correct IS NULL",
    )
    op.drop_column('attempts', 'grading_details')
    op.drop_column('submissions', 'request_fingerprint')
