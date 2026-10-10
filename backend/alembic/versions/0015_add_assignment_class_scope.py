"""add assignment class scope

Revision ID: 0015
Revises: 0014
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "assignments",
        sa.Column("class_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_assignments_class_id_school_classes",
        "assignments",
        "school_classes",
        ["class_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index("ix_assignments_class_id", "assignments", ["class_id"])
    op.execute(
        "UPDATE assignments SET class_id = ("
        "SELECT id FROM school_classes WHERE name = '六年级 Demo 班' LIMIT 1"
        ") WHERE class_id IS NULL"
    )


def downgrade() -> None:
    op.drop_index("ix_assignments_class_id", table_name="assignments")
    op.drop_constraint(
        "fk_assignments_class_id_school_classes", "assignments", type_="foreignkey"
    )
    op.drop_column("assignments", "class_id")
