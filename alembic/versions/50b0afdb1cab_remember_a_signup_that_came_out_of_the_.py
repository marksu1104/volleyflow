"""remember a signup that came out of the queue

A signup that came off the waitlist — promoted automatically, or named
as somebody's 代打 while waiting — gave up a place in the queue to take
its slot. If the slot is later taken back, that place is owed back, at
the position originally held.

Cancelling an arranged substitute used to delete them from the game
outright: they had left the queue and got nothing in return. Inferring
it was not possible, because a substitute typed in by name was never in
the queue and must *not* be put into one.

Nullable with no default, so it costs nothing on existing rows — none of
which can be known to have come from the queue.

Revision ID: 50b0afdb1cab
Revises: 6f13559e49d7
Create Date: 2026-09-11

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "50b0afdb1cab"
down_revision: str | Sequence[str] | None = "6f13559e49d7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "drop_ins", sa.Column("from_waitlist_at", sa.DateTime(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("drop_ins", "from_waitlist_at")
