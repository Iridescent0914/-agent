"""create knowledge and question tables"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "knowledge_points",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("code", sa.String(160), nullable=False, unique=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("chapter_code", sa.String(160), nullable=False),
        sa.Column("chapter_name", sa.String(160), nullable=False),
        sa.Column("section", sa.String(255)),
        sa.Column(
            "parent_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("knowledge_points.id", ondelete="SET NULL"),
        ),
    )
    op.create_table(
        "exam_points",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("code", sa.String(180), nullable=False, unique=True),
        sa.Column("name", sa.String(500), nullable=False),
        sa.Column(
            "knowledge_point_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("knowledge_points.id", ondelete="RESTRICT"),
            nullable=False,
        ),
    )
    op.create_table(
        "questions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("original_qid", sa.String(180), nullable=False, unique=True),
        sa.Column("schema_version", sa.String(32), nullable=False),
        sa.Column("chapter_code", sa.String(160), nullable=False),
        sa.Column("chapter_name", sa.String(160), nullable=False),
        sa.Column("section", sa.String(255), nullable=False),
        sa.Column("question_type", sa.String(32), nullable=False),
        sa.Column("difficulty", sa.SmallInteger(), nullable=False),
        sa.Column(
            "main_knowledge_point_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("knowledge_points.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("question_text", sa.Text(), nullable=False),
        sa.Column("options", postgresql.JSONB()),
        sa.Column("correct_answer", postgresql.JSONB(), nullable=False),
        sa.Column("reference_answer_raw", sa.Text(), nullable=False),
        sa.Column("answer_match_type", sa.String(32), nullable=False),
        sa.Column("answer_normalization_rule", postgresql.JSONB(), nullable=False),
        sa.Column("solution", sa.Text()),
        sa.Column("wrong_analysis", sa.Text()),
        sa.Column("estimated_score_rate", sa.String(64)),
        sa.Column("has_image", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("has_equation", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("assets", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("source_file", sa.Text(), nullable=False),
        sa.Column("source_question_number", sa.Integer(), nullable=False),
        sa.Column("source_review_status", sa.String(32), nullable=False),
        sa.Column("grading_readiness", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("difficulty BETWEEN 1 AND 5", name="ck_questions_difficulty"),
        sa.CheckConstraint(
            "question_type IN ('choice', 'fill_blank')", name="ck_questions_type"
        ),
    )
    op.create_index(
        "ix_questions_filters", "questions", ["chapter_code", "question_type", "difficulty"]
    )
    op.create_table(
        "question_exam_points",
        sa.Column(
            "question_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("questions.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "exam_point_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("exam_points.id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        sa.Column("relation_type", sa.String(16), nullable=False),
        sa.CheckConstraint(
            "relation_type IN ('main', 'related')", name="ck_qep_relation_type"
        ),
    )
    op.create_index(
        "uq_question_one_main_exam_point",
        "question_exam_points",
        ["question_id"],
        unique=True,
        postgresql_where=sa.text("relation_type = 'main'"),
    )
    op.create_index(
        "ix_qep_exam_question",
        "question_exam_points",
        ["exam_point_id", "question_id"],
    )


def downgrade() -> None:
    op.drop_table("question_exam_points")
    op.drop_index("ix_questions_filters", table_name="questions")
    op.drop_table("questions")
    op.drop_table("exam_points")
    op.drop_table("knowledge_points")
