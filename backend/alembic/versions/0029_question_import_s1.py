"""Add private bulk-import staging and question provenance.

Revision ID: 0029
Revises: 0028
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
import logging

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None


def _fingerprint(text: str, options: object, assets: object) -> str:
    normalized = re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text)).strip()
    raw = json.dumps(
        {"text": normalized, "options": options or [], "assets": assets or []},
        ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def upgrade() -> None:
    op.create_table(
        "question_import_batches",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("owner_scope", sa.String(80), nullable=False),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("catalog_snapshot_sha256", sa.String(64), nullable=False),
        sa.Column("source_mode", sa.String(32), nullable=False),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("stage", sa.String(40), nullable=False),
        sa.Column("files", postgresql.JSONB, nullable=False),
        sa.Column("checkpoint", postgresql.JSONB, nullable=False),
        sa.Column("error_code", sa.String(80)),
        sa.Column("error_message", sa.Text),
        sa.Column("worker_token", postgresql.UUID(as_uuid=True)),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True)),
        sa.Column("attempt_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("files_purged_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("uq_question_import_active_request", "question_import_batches",
                    ["owner_scope", "request_fingerprint"], unique=True,
                    postgresql_where=sa.text("files_purged_at IS NULL"))
    op.create_index("ix_question_import_batch_lease", "question_import_batches", ["status", "lease_until"])
    op.create_table(
        "question_import_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("batch_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("question_import_batches.id", ondelete="CASCADE"), nullable=False),
        sa.Column("ordinal", sa.Integer, nullable=False),
        sa.Column("revision", sa.Integer, nullable=False, server_default="1"),
        sa.Column("payload", postgresql.JSONB, nullable=False),
        sa.Column("is_fake", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("batch_id", "ordinal", name="uq_question_import_item_ordinal"),
    )
    op.create_index("ix_question_import_items_batch_id", "question_import_items", ["batch_id"])
    op.add_column("questions", sa.Column("content_fingerprint", sa.String(64)))
    op.add_column("questions", sa.Column("import_batch_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("question_import_batches.id", ondelete="SET NULL")))
    op.add_column("questions", sa.Column("import_item_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("question_import_items.id", ondelete="SET NULL")))
    op.create_index("ix_questions_content_fingerprint", "questions", ["content_fingerprint"])
    op.create_index("ix_questions_import_batch_id", "questions", ["import_batch_id"])
    op.create_index("ix_questions_import_item_id", "questions", ["import_item_id"])
    connection = op.get_bind()
    rows = connection.execute(sa.text("SELECT id, question_text, options, assets FROM questions")).all()
    for question_id, text, options, assets in rows:
        connection.execute(sa.text("UPDATE questions SET content_fingerprint = :fingerprint WHERE id = :id"), {
            "id": question_id, "fingerprint": _fingerprint(text, options, assets),
        })
    duplicate_groups = connection.execute(sa.text(
        "SELECT COUNT(*) FROM (SELECT content_fingerprint FROM questions "
        "GROUP BY content_fingerprint HAVING COUNT(*) > 1) AS duplicates"
    )).scalar_one()
    logging.getLogger(__name__).info(
        "Historical question fingerprint duplicate groups: %s; no rows changed", duplicate_groups
    )


def downgrade() -> None:
    op.drop_index("ix_questions_import_item_id", table_name="questions")
    op.drop_index("ix_questions_import_batch_id", table_name="questions")
    op.drop_index("ix_questions_content_fingerprint", table_name="questions")
    op.drop_column("questions", "import_item_id")
    op.drop_column("questions", "import_batch_id")
    op.drop_column("questions", "content_fingerprint")
    op.drop_index("ix_question_import_items_batch_id", table_name="question_import_items")
    op.drop_table("question_import_items")
    op.drop_index("ix_question_import_batch_lease", table_name="question_import_batches")
    op.drop_index("uq_question_import_active_request", table_name="question_import_batches")
    op.drop_table("question_import_batches")
