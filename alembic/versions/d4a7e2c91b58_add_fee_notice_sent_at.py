"""add fee notice sent at

Revision ID: d4a7e2c91b58
Revises: c2f81a4e7b63
Create Date: 2026-10-02 17:20:00.000000

When each member was sent a season's 繳費通知. A LINE message cannot be
taken back, so each member receives it at most once a season, and this
is the record that enforces it. Nullable with no default: nobody has
been sent one yet, which is exactly what NULL says.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd4a7e2c91b58'
down_revision: Union[str, Sequence[str], None] = 'c2f81a4e7b63'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'season_members',
        sa.Column('fee_notice_sent_at', sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('season_members', 'fee_notice_sent_at')
