"""tell each promotion once

Revision ID: e4b91c7d2a50
Revises: c9d1e5f2a374
Create Date: 2026-10-14 09:00:00.000000

Promotion notices move from the routes to the deadline job, which tells
each signup once and records when in `drop_ins.promotion_notice_sent_at`.

Everybody already playing off the queue in a game whose deadline notices
have gone out was told under the old rule — at the deadline batch, or the
moment they were promoted after it — so they are marked as told. Without
this the job's first run would tell every one of them again.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e4b91c7d2a50'
down_revision: Union[str, Sequence[str], None] = 'c9d1e5f2a374'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Who was already told under the old rule. A constant so
# tests/api/test_notifications.py can run exactly this against a real
# roster: get it wrong and the first run after deploy tells them all again.
MARK_ALREADY_TOLD = (
    "UPDATE drop_ins SET promotion_notice_sent_at = ("
    "  SELECT games.roster_notice_sent_at FROM games WHERE games.id = drop_ins.game_id"
    ") "
    "WHERE queued_at IS NOT NULL AND status = 'playing' AND cancelled_at IS NULL "
    "AND game_id IN (SELECT id FROM games WHERE roster_notice_sent_at IS NOT NULL)"
)


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'drop_ins', sa.Column('promotion_notice_sent_at', sa.DateTime(), nullable=True)
    )
    op.execute(MARK_ALREADY_TOLD)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('drop_ins', 'promotion_notice_sent_at')
