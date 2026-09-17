"""add per-game location and time

Revision ID: e1c7a4b93d20
Revises: b4e2d8a6c1f9
Create Date: 2026-09-17 06:40:11.284517

Asked for on 2026-09-17: 「不應該只有改日期，要可以臨時更改場次資訊，像是
場地名稱、時間」. All three are nullable, and NULL means "same as the
season" — so every game that exists today keeps behaving exactly as it
does now without being touched.

Nothing here is a billing column. The season's own location and time
pair are display-only (see SeasonRow), and docs/billing-rules.md says
an edit that changes nobody's math — "e.g. changing the venue
location" — writes no ledger entry. Per-game price is a separate
question and deliberately not part of this migration.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e1c7a4b93d20'
down_revision: Union[str, Sequence[str], None] = 'b4e2d8a6c1f9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('games', sa.Column('location', sa.String(), nullable=True))
    op.add_column('games', sa.Column('start_time', sa.Time(), nullable=True))
    op.add_column('games', sa.Column('end_time', sa.Time(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('games', 'end_time')
    op.drop_column('games', 'start_time')
    op.drop_column('games', 'location')
