"""add independent school class management

Revision ID: 0014
Revises: 0013
"""

from __future__ import annotations

import uuid

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "school_classes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(120), nullable=False, unique=True),
        sa.Column("grade", sa.String(40), nullable=False, server_default="未设置"),
        sa.Column("school_year", sa.String(40), nullable=False, server_default="未设置"),
        sa.Column("teacher_name", sa.String(80)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.add_column(
        "students",
        sa.Column("class_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_students_class_id_school_classes",
        "students",
        "school_classes",
        ["class_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_students_class_id", "students", ["class_id"])

    bind = op.get_bind()
    class_names = [
        row[0]
        for row in bind.execute(
            sa.text("SELECT DISTINCT class_name FROM students ORDER BY class_name")
        )
    ]
    for class_name in class_names:
        class_id = uuid.uuid4()
        bind.execute(
            sa.text(
                "INSERT INTO school_classes "
                "(id, name, grade, school_year) "
                "VALUES (:id, :name, :grade, :school_year)"
            ),
            {
                "id": class_id,
                "name": class_name,
                "grade": "未设置",
                "school_year": "未设置",
            },
        )
        bind.execute(
            sa.text("UPDATE students SET class_id = :id WHERE class_name = :name"),
            {"id": class_id, "name": class_name},
        )


def downgrade() -> None:
    op.drop_index("ix_students_class_id", table_name="students")
    op.drop_constraint(
        "fk_students_class_id_school_classes", "students", type_="foreignkey"
    )
    op.drop_column("students", "class_id")
    op.drop_table("school_classes")
