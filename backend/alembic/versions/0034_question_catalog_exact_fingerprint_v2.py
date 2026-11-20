"""Backfill a separate, math-aware catalog fingerprint for S4 exact matching.

Revision ID: 0034
Revises: 0033
"""

from alembic import op
import sqlalchemy as sa

from app.services.question_fingerprint_v2 import catalog_content_key, fingerprint


revision = "0034"
down_revision = "0033"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("questions", sa.Column("exact_fingerprint_v2", sa.String(64), nullable=True))
    op.create_index("ix_questions_exact_fingerprint_v2", "questions", ["exact_fingerprint_v2"])
    connection = op.get_bind()
    rows = connection.execute(sa.text(
        "SELECT id, question_text, options, assets, has_image FROM questions"
    ))
    for question_id, question_text, options, assets, has_image in rows:
        value = fingerprint(catalog_content_key(question_text, options, assets, has_image=has_image))
        if value is not None:
            connection.execute(sa.text(
                "UPDATE questions SET exact_fingerprint_v2 = :value WHERE id = :id"
            ), {"id": question_id, "value": value})


def downgrade() -> None:
    op.drop_index("ix_questions_exact_fingerprint_v2", table_name="questions")
    op.drop_column("questions", "exact_fingerprint_v2")
