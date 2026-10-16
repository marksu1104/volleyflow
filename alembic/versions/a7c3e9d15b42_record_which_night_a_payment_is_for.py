"""record which night a payment is for

Revision ID: a7c3e9d15b42
Revises: e4b91c7d2a50
Create Date: 2026-10-16 09:00:00.000000

`ledger_entries.game_id`: the night a drop-in payment was taken for on
當天臨打, so paying for one night no longer reads as paying for all of
them. Null for every existing entry — they were never tied to a night,
and a night with no payment of its own still counts as paid once the
player owes nothing for the season.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a7c3e9d15b42'
down_revision: Union[str, Sequence[str], None] = 'e4b91c7d2a50'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'ledger_entries',
        sa.Column('game_id', sa.Integer(), sa.ForeignKey('games.id'), nullable=True),
    )
    op.create_index('ix_ledger_entries_game_id', 'ledger_entries', ['game_id'])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_ledger_entries_game_id', table_name='ledger_entries')
    op.drop_column('ledger_entries', 'game_id')
