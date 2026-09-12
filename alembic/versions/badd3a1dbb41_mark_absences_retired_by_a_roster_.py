"""Mark absences retired by a roster removal

Revision ID: badd3a1dbb41
Revises: dd5f57cf2081
Create Date: 2026-09-12 20:43:54.620545

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'badd3a1dbb41'
down_revision: Union[str, Sequence[str], None] = 'dd5f57cf2081'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Nullable, and no backfill: NULL means "cancelled by the member
    # themselves", which is the right reading of every absence written
    # before this column existed. An absence retired by a removal that
    # happened before today stays closed, same as it is now — the only
    # thing this changes is what happens from here on.
    op.add_column('absences', sa.Column('retired_at', sa.DateTime(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('absences', 'retired_at')
