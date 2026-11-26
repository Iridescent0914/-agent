"""Rebuild S4 fingerprints after supporting legacy dict-shaped options.

Revision ID: 0043
Revises: 0042
"""

from alembic import op
import sqlalchemy as sa

from app.services.question_fingerprint_v2 import catalog_content_key, fingerprint


revision = "0043"
down_revision = "0042"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    rows = connection.execute(sa.text(
        "SELECT id, question_text, options, assets, has_image FROM questions "
        "WHERE archived_at IS NULL"
    ))
    for question_id, question_text, options, assets, has_image in rows:
        value = fingerprint(catalog_content_key(question_text, options, assets, has_image=has_image))
        connection.execute(sa.text(
            "UPDATE questions SET exact_fingerprint_v2 = :value WHERE id = :id"
        ), {"id": question_id, "value": value})


def downgrade() -> None:
    # Keeping the corrected fingerprint is safe for older readers.
    pass
