"""Let a mistaken payment be undone by an opposite entry

Revision ID: 9d4f2b6a1c83
Revises: 7a1c3e5b9d20
Create Date: 2026-09-16 04:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9d4f2b6a1c83'
down_revision: Union[str, Sequence[str], None] = '7a1c3e5b9d20'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'ledger_entries',
        sa.Column('reverses_entry_id', sa.Integer(), sa.ForeignKey('ledger_entries.id'), nullable=True),
    )
    # One undo per payment: a repeated 復原 must not reverse it twice.
    op.create_index(
        'uq_ledger_entries_reverses_entry_id',
        'ledger_entries',
        ['reverses_entry_id'],
        unique=True,
        postgresql_where=sa.text('reverses_entry_id IS NOT NULL'),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('uq_ledger_entries_reverses_entry_id', table_name='ledger_entries')
    op.drop_column('ledger_entries', 'reverses_entry_id')
