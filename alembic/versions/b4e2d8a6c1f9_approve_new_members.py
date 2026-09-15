"""Hold people who join by link until the organizer approves them

Revision ID: b4e2d8a6c1f9
Revises: 9d4f2b6a1c83
Create Date: 2026-09-16 05:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b4e2d8a6c1f9'
down_revision: Union[str, Sequence[str], None] = '9d4f2b6a1c83'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Everybody already in a club stays in: only joins from now on wait.
    op.add_column(
        'club_members',
        sa.Column('status', sa.String(), nullable=False, server_default='active'),
    )
    op.create_check_constraint(
        'ck_club_members_status', 'club_members', "status IN ('pending', 'active')"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint('ck_club_members_status', 'club_members', type_='check')
    op.drop_column('club_members', 'status')
