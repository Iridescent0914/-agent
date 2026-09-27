'''add business deletion for questions and students'''

from alembic import op
import sqlalchemy as sa


revision = '0010'
down_revision = '0009'
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table_name in ('questions', 'students'):
        op.add_column(
            table_name,
            sa.Column('archived_at', sa.DateTime(timezone=True), nullable=True),
        )
        op.add_column(
            table_name,
            sa.Column('archived_by', sa.String(80), nullable=True),
        )
        op.create_check_constraint(
            f'ck_{table_name}_archive_complete',
            table_name,
            '(archived_at IS NULL AND archived_by IS NULL) OR '
            '(archived_at IS NOT NULL AND length(btrim(archived_by)) > 0)',
        )
        op.create_index(
            f'ix_{table_name}_archived_at',
            table_name,
            ['archived_at'],
        )


def downgrade() -> None:
    for table_name in ('students', 'questions'):
        op.drop_index(f'ix_{table_name}_archived_at', table_name=table_name)
        op.drop_constraint(
            f'ck_{table_name}_archive_complete',
            table_name,
            type_='check',
        )
        op.drop_column(table_name, 'archived_by')
        op.drop_column(table_name, 'archived_at')
