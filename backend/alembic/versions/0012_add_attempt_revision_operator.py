'''record the concrete operator for M4 attempt revisions'''

from alembic import op
import sqlalchemy as sa


revision = '0012'
down_revision = '0011'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'attempt_revisions',
        sa.Column('operator_id', sa.String(80), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('attempt_revisions', 'operator_id')
