"""Indexed S4 nearest-neighbour search.

Revision ID: 0039
Revises: 0038
"""

from alembic import op

revision = "0039"
down_revision = "0038"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute("""CREATE INDEX ix_questions_active_text_trgm ON questions USING gist
        ((question_text || ' ' || coalesce(options::text, '')) gist_trgm_ops)
        WHERE archived_at IS NULL""")


def downgrade() -> None:
    op.drop_index("ix_questions_active_text_trgm", table_name="questions")
