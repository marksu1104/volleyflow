"""remember a drop-in absorbed by membership

Adding somebody to a season's fixed roster cancels the drop-ins they had
already signed up for, so the same night isn't charged twice. Taking
them off the roster again has to put those back — but `cancelled_at`
alone can't tell a signup the system absorbed from one the player
cancelled themselves, and resurrecting the latter would put somebody on
a roster they had deliberately left.

Reported from real use: a member added to the roster and removed again
lost the record of a night they had actually played, and the fee owed
for it went with it.

Nullable with no default, so it costs nothing on the existing rows —
every drop-in already in the table was cancelled by a person, if at all.

Revision ID: 6f13559e49d7
Revises: c803bebc66c0
Create Date: 2026-09-10 06:53:31.082498

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "6f13559e49d7"
down_revision: str | Sequence[str] | None = "c803bebc66c0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("drop_ins", sa.Column("absorbed_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("drop_ins", "absorbed_at")
