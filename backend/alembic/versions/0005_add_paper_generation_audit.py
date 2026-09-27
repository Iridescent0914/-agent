"""add M3 paper generation and editing audit fields"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("assignments", sa.Column("idempotency_key", sa.String(100), nullable=True))
    op.create_unique_constraint("uq_assignments_idempotency_key", "assignments", ["idempotency_key"])
    op.add_column("assignments", sa.Column("generation_config", postgresql.JSONB(), nullable=True))
    op.add_column("paper_instances", sa.Column("recommendation_audit", postgresql.JSONB(), nullable=True))
    op.add_column("paper_instances", sa.Column("edit_history", postgresql.JSONB(), nullable=False, server_default="[]"))
    op.add_column("paper_instances", sa.Column("edit_revision", sa.Integer(), nullable=False, server_default="1"))
    op.create_check_constraint("ck_paper_edit_revision_positive", "paper_instances", "edit_revision > 0")
    op.create_check_constraint(
        "ck_published_paper_identity", "paper_instances",
        "status <> 'published' OR (instance_code IS NOT NULL AND published_at IS NOT NULL)",
    )
    op.add_column("paper_instance_items", sa.Column("selection_audit", postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("paper_instance_items", "selection_audit")
    op.drop_constraint("ck_published_paper_identity", "paper_instances", type_="check")
    op.drop_constraint("ck_paper_edit_revision_positive", "paper_instances", type_="check")
    op.drop_column("paper_instances", "edit_revision")
    op.drop_column("paper_instances", "edit_history")
    op.drop_column("paper_instances", "recommendation_audit")
    op.drop_column("assignments", "generation_config")
    op.drop_constraint("uq_assignments_idempotency_key", "assignments", type_="unique")
    op.drop_column("assignments", "idempotency_key")
