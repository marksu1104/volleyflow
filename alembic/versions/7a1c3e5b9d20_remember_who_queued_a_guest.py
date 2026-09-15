"""Remember who put a guest in the queue

Revision ID: 7a1c3e5b9d20
Revises: 5c2e9a1d7f40
Create Date: 2026-09-16 03:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7a1c3e5b9d20'
down_revision: Union[str, Sequence[str], None] = '5c2e9a1d7f40'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Nullable, no backfill: nobody recorded who queued the entries already here.
    op.add_column(
        'waitlist_entries',
        sa.Column('brought_by_player_id', sa.Integer(), sa.ForeignKey('players.id'), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('waitlist_entries', 'brought_by_player_id')
