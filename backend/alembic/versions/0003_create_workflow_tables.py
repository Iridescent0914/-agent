"""create assignment, paper and attempt tables"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "assignments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("seed_key", sa.String(100), unique=True),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("chapter_code", sa.String(160), nullable=False),
        sa.Column("teaching_scope", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("assignment_type", sa.String(32), nullable=False, server_default="practice"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_table(
        "paper_instances",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "assignment_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("assignments.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "student_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("students.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="draft"),
        sa.Column("instance_code", sa.String(64), unique=True),
        sa.Column(
            "supersedes_instance_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("paper_instances.id", ondelete="SET NULL"),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint(
            "assignment_id", "student_id", "version_no", name="uq_paper_assignment_student_version"
        ),
        sa.CheckConstraint("version_no > 0", name="ck_paper_version_positive"),
        sa.CheckConstraint("status IN ('draft', 'published')", name="ck_paper_status"),
    )
    op.create_table(
        "paper_instance_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "paper_instance_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("paper_instances.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "question_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("questions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("order_no", sa.Integer(), nullable=False),
        sa.Column("question_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("options_snapshot", postgresql.JSONB()),
        sa.Column("answer_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("answer_rule_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("exam_point_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("answer_region", postgresql.JSONB()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("paper_instance_id", "order_no", name="uq_paper_item_order"),
        sa.CheckConstraint("order_no > 0", name="ck_paper_item_order_positive"),
    )
    op.create_table(
        "submissions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "student_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("students.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "paper_instance_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("paper_instances.id", ondelete="RESTRICT"),
            nullable=False,
            unique=True,
        ),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("idempotency_key", sa.String(100), nullable=False, unique=True),
        sa.Column("attachments", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("source IN ('manual', 'image')", name="ck_submission_source"),
    )
    op.create_table(
        "attempts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "submission_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("submissions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "student_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("students.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "paper_instance_item_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("paper_instance_items.id", ondelete="RESTRICT"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "question_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("questions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("student_answer", postgresql.JSONB()),
        sa.Column("normalized_answer", postgresql.JSONB()),
        sa.Column("grade_status", sa.String(24), nullable=False),
        sa.Column("is_correct", sa.Boolean()),
        sa.Column("ocr_confidence", sa.Numeric(5, 4)),
        sa.Column("recognition_status", sa.String(24)),
        sa.Column("manual_override", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("revision_no", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("answered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "grade_status IN ('auto_confirmed', 'needs_review', 'teacher_confirmed')",
            name="ck_attempt_grade_status",
        ),
        sa.CheckConstraint(
            "grade_status <> 'needs_review' OR is_correct IS NULL",
            name="ck_needs_review_unknown",
        ),
        sa.CheckConstraint("revision_no > 0", name="ck_attempt_revision_positive"),
    )
    op.create_index(
        "ix_attempts_student_answered", "attempts", ["student_id", "answered_at"]
    )
    op.create_table(
        "attempt_revisions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "attempt_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("attempts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("revision_no", sa.Integer(), nullable=False),
        sa.Column("before_state", postgresql.JSONB()),
        sa.Column("after_state", postgresql.JSONB(), nullable=False),
        sa.Column("operator_type", sa.String(32), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("attempt_id", "revision_no", name="uq_attempt_revision"),
        sa.CheckConstraint("revision_no > 0", name="ck_attempt_revision_log_positive"),
    )


def downgrade() -> None:
    op.drop_table("attempt_revisions")
    op.drop_index("ix_attempts_student_answered", table_name="attempts")
    op.drop_table("attempts")
    op.drop_table("submissions")
    op.drop_table("paper_instance_items")
    op.drop_table("paper_instances")
    op.drop_table("assignments")
