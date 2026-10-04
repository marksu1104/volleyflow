"""add roster notice sent at

Revision ID: b7e4c2a9d816
Revises: a6d2f8c3e915
Create Date: 2026-10-05 15:00:00.000000

When a game's deadline notices were sent: the roster status to the
organizer and the promotion notices held until the deadline. A job runs
every ten minutes and sends for any game past its deadline with this
still empty, so this is what makes it send once. Nullable, no backfill:
games already past their deadline are left alone by the job's own date
filter rather than marked here.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b7e4c2a9d816'
down_revision: Union[str, Sequence[str], None] = 'a6d2f8c3e915'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('games', sa.Column('roster_notice_sent_at', sa.DateTime(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('games', 'roster_notice_sent_at')
