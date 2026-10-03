"""drop queued by on drop ins

Revision ID: f3c9a1e6b204
Revises: e8b3c5d07a12
Create Date: 2026-10-04 10:30:00.000000

Undoes e8b3c5d07a12 a day later. That column kept a second copy of who
had signed somebody up, because naming them a 代打 overwrote the first.
The bringer is now never overwritten, so there is nothing to keep a copy
of. Nothing reads the column any more and nothing was written to it in
the day it existed but the original bringer, which the drop-in's own
brought_by_player_id now keeps.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f3c9a1e6b204'
down_revision: Union[str, Sequence[str], None] = 'e8b3c5d07a12'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_column('drop_ins', 'queued_by_player_id')


def downgrade() -> None:
    """Downgrade schema."""
    op.add_column(
        'drop_ins',
        sa.Column('queued_by_player_id', sa.Integer(), sa.ForeignKey('players.id'), nullable=True),
    )
