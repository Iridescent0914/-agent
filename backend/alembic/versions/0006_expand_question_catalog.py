"""expand the question catalog and preserve teaching order"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("knowledge_points", sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("exam_points", sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"))
    op.create_check_constraint("ck_knowledge_points_sort_order", "knowledge_points", "sort_order >= 0")
    op.create_check_constraint("ck_exam_points_sort_order", "exam_points", "sort_order >= 0")

    op.drop_constraint("ck_questions_type", "questions", type_="check")
    op.create_check_constraint(
        "ck_questions_type",
        "questions",
        "question_type IN ('choice', 'fill_blank', 'calculation', 'solution', 'true_false', 'unknown')",
    )
    op.alter_column("questions", "correct_answer", nullable=True)
    op.alter_column("questions", "answer_match_type", nullable=True)
    op.add_column("questions", sa.Column("import_status", sa.String(32), nullable=False, server_default="parsed"))
    op.add_column(
        "questions",
        sa.Column("import_issues", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
    )
    op.create_check_constraint(
        "ck_machine_ready_answer",
        "questions",
        "grading_readiness <> 'machine_ready' OR "
        "(correct_answer IS NOT NULL AND answer_match_type IS NOT NULL)",
    )


def downgrade() -> None:
    op.drop_constraint("ck_machine_ready_answer", "questions", type_="check")
    op.drop_column("questions", "import_issues")
    op.drop_column("questions", "import_status")
    op.execute(
        "DELETE FROM questions WHERE question_type NOT IN ('choice', 'fill_blank') "
        "OR answer_match_type IS NULL"
    )
    op.alter_column("questions", "answer_match_type", nullable=False)
    op.alter_column("questions", "correct_answer", nullable=False)
    op.drop_constraint("ck_questions_type", "questions", type_="check")
    op.create_check_constraint("ck_questions_type", "questions", "question_type IN ('choice', 'fill_blank')")
    op.drop_constraint("ck_exam_points_sort_order", "exam_points", type_="check")
    op.drop_constraint("ck_knowledge_points_sort_order", "knowledge_points", type_="check")
    op.drop_column("exam_points", "sort_order")
    op.drop_column("knowledge_points", "sort_order")
