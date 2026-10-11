"""Allow only one active recognition run per paper.

Revision ID: 0019
Revises: 0018
"""

from alembic import op
import sqlalchemy as sa


revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Keep the newest recoverable run. Older concurrent runs were never visible
    # after refresh, so expire them before enforcing the invariant.
    op.execute(
        """
        WITH ranked AS (
            SELECT id,
                   row_number() OVER (
                       PARTITION BY paper_instance_id
                       ORDER BY created_at DESC, id DESC
                   ) AS position
            FROM recognition_runs
            WHERE status IN ('uploaded', 'processing', 'ready_for_review', 'failed')
        )
        UPDATE recognition_runs AS run
        SET status = 'discarded',
            processing_token = NULL,
            completed_at = COALESCE(run.completed_at, now()),
            retention_until = LEAST(COALESCE(run.retention_until, now()), now())
        FROM ranked
        WHERE run.id = ranked.id AND ranked.position > 1
        """
    )
    op.create_index(
        "uq_recognition_run_active_per_paper",
        "recognition_runs",
        ["paper_instance_id"],
        unique=True,
        postgresql_where=sa.text(
            "status IN ('uploaded', 'processing', 'ready_for_review', 'failed')"
        ),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_recognition_run_active_per_paper", table_name="recognition_runs"
    )
