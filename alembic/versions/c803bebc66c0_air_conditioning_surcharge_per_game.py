"""air conditioning surcharge per game

Revision ID: c803bebc66c0
Revises: 4560ad02d5d6
Create Date: 2026-09-10 00:25:04.603679

Both columns are NOT NULL with a server default of the "no air
conditioning" value, so every row that already exists gets it without a
backfill and every existing season keeps charging exactly what it
charged before — pricing.shares_by_game with ac_surcharge 0 is the flat
split it has always used.

The server_default matters: autogenerate emitted these as NOT NULL with
no default, which fails outright on a table that already has rows. It
also missed the CHECK constraint, which is added here by hand.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c803bebc66c0"
down_revision: str | Sequence[str] | None = "4560ad02d5d6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CHECK = "ck_seasons_ac_surcharge_non_negative"


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "games",
        sa.Column(
            "air_conditioned",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        "seasons",
        sa.Column(
            "ac_surcharge",
            sa.Numeric(precision=10, scale=0),
            nullable=False,
            server_default="0",
        ),
    )
    op.create_check_constraint(_CHECK, "seasons", "ac_surcharge >= 0")


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(_CHECK, "seasons", type_="check")
    op.drop_column("seasons", "ac_surcharge")
    op.drop_column("games", "air_conditioned")
