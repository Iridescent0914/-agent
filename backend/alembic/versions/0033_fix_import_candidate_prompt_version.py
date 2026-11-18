"""Align S2 candidate audit versions with their batch checkpoints.

Revision ID: 0033
Revises: 0032
"""

from alembic import op


revision = "0033"
down_revision = "0032"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        UPDATE question_import_items AS item
        SET payload = jsonb_set(
            item.payload,
            '{extraction,prompt_version}',
            to_jsonb(batch.checkpoint #>> '{s2,prompt_version}'),
            true
        )
        FROM question_import_batches AS batch
        WHERE item.batch_id = batch.id
          AND batch.source_mode = 'combined'
          AND batch.checkpoint #>> '{s2,prompt_version}' IN
              ('question-import-combined-v8', 'question-import-combined-v9')
          AND jsonb_typeof(item.payload->'extraction') = 'object'
          AND item.payload #>> '{extraction,prompt_version}' IS DISTINCT FROM
              batch.checkpoint #>> '{s2,prompt_version}'
    """)


def downgrade() -> None:
    # The old candidate value was incorrect; its original value cannot be inferred safely.
    pass
