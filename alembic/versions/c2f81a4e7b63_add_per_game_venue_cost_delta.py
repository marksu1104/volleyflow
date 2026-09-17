"""add per-game venue cost delta

Revision ID: c2f81a4e7b63
Revises: e1c7a4b93d20
Create Date: 2026-09-17 15:12:44.903118

What one night costs the club above (or below) a normal night, for the
evening the booking moves to a different court — 「有時候會換場地，那個
場地的價錢不同」. Priced exactly like ac_surcharge: taken out of the season
total before the even split, added back on the night it belongs to. See
docs/billing-rules.md, "A different venue for one night".

NOT NULL with a server default of 0, so every game already on the books
keeps costing exactly what it costs today and the whole calculation
collapses back to what it was. A negative value is deliberately allowed
— a cheaper hall is as real as a dearer one — so there is no check
constraint here; only the aggregate is guarded, in pricing.shares_by_game.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c2f81a4e7b63'
down_revision: Union[str, Sequence[str], None] = 'e1c7a4b93d20'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'games',
        sa.Column(
            'venue_cost_delta',
            sa.Numeric(10, 0),
            nullable=False,
            server_default='0',
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('games', 'venue_cost_delta')
