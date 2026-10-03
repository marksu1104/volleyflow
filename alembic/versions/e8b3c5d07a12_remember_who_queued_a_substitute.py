"""remember who queued a substitute

Revision ID: e8b3c5d07a12
Revises: d4a7e2c91b58
Create Date: 2026-10-03 16:40:00.000000

Who had signed somebody up while they waited, kept on the drop-in when
they are named a 代打 straight from the queue. Naming a 代打 records the
absent member as the bringer, so without this the queue showed the
wrong name once the 代打 was released back to it. Nullable, no
backfill: the original bringer of past rows was never recorded.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e8b3c5d07a12'
down_revision: Union[str, Sequence[str], None] = 'd4a7e2c91b58'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'drop_ins',
        sa.Column('queued_by_player_id', sa.Integer(), sa.ForeignKey('players.id'), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('drop_ins', 'queued_by_player_id')
