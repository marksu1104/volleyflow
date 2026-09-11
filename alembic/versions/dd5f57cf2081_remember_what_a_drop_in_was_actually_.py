"""remember what a drop-in was actually charged

Cancelling a drop-in used to refund `share_per_game` recomputed as of the
moment of cancellation, not the amount actually charged at signup. Those
differ whenever the organizer flips a game's air conditioning or edits
the season's capacity in between — both move the share — so a signup
charged $667 for a cooled night and refunded $572 once the setting was
corrected left a $95 gap that never balances, sitting against somebody no
longer connected to the game at all.

Found by a random sweep (tests/api/test_fuzz.py), not by a real invoice
not adding up.

Nullable with no default: every drop-in already in the table keeps
refunding at the recomputed share, same as before this column existed —
see routes._record_drop_in_charge.

Revision ID: dd5f57cf2081
Revises: 50b0afdb1cab
Create Date: 2026-09-12 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "dd5f57cf2081"
down_revision: str | Sequence[str] | None = "50b0afdb1cab"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "drop_ins", sa.Column("charged_amount", sa.Numeric(10, 0), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("drop_ins", "charged_amount")
