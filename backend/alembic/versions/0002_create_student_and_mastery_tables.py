"""create student and mastery tables"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "students",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("student_code", sa.String(32), nullable=False, unique=True),
        sa.Column("display_name", sa.String(80), nullable=False),
        sa.Column("class_name", sa.String(120), nullable=False),
        sa.Column("is_demo", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_table(
        "student_masteries",
        sa.Column(
            "student_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("students.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "exam_point_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("exam_points.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("mastery", sa.Numeric(5, 4), nullable=False),
        sa.Column("confidence_level", sa.String(16), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("correct_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("recent_wrong_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_practiced_at", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("mastery BETWEEN 0 AND 1", name="ck_mastery_range"),
        sa.CheckConstraint(
            "confidence_level IN ('low', 'medium', 'high')", name="ck_confidence_level"
        ),
        sa.CheckConstraint(
            "attempt_count >= 0 AND correct_count >= 0 AND recent_wrong_count >= 0",
            name="ck_mastery_counts_nonnegative",
        ),
    )


def downgrade() -> None:
    op.drop_table("student_masteries")
    op.drop_table("students")
