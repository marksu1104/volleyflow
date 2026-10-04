"""change deadline in hours

Revision ID: a6d2f8c3e915
Revises: f3c9a1e6b204
Create Date: 2026-10-05 11:00:00.000000

The change deadline becomes "N hours before the game starts", default
24, and every season has one. It was "N days before", closing at
midnight, and optional; the notices sent when it passes need a moment
that is both always there and a reasonable time to receive a message.
Existing settings carry over as days x 24, and "no deadline" becomes 24.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a6d2f8c3e915'
down_revision: Union[str, Sequence[str], None] = 'f3c9a1e6b204'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'seasons',
        sa.Column(
            'change_deadline_hours', sa.Integer(), nullable=False, server_default='24'
        ),
    )
    op.execute(
        "UPDATE seasons SET change_deadline_hours = change_deadline_days * 24 "
        "WHERE change_deadline_days IS NOT NULL"
    )
    op.drop_constraint('ck_seasons_change_deadline_non_negative', 'seasons', type_='check')
    op.drop_column('seasons', 'change_deadline_days')
    op.create_check_constraint(
        'ck_seasons_change_deadline_non_negative', 'seasons', 'change_deadline_hours >= 0'
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint('ck_seasons_change_deadline_non_negative', 'seasons', type_='check')
    op.add_column('seasons', sa.Column('change_deadline_days', sa.Integer(), nullable=True))
    op.execute("UPDATE seasons SET change_deadline_days = change_deadline_hours / 24")
    op.drop_column('seasons', 'change_deadline_hours')
    op.create_check_constraint(
        'ck_seasons_change_deadline_non_negative', 'seasons', 'change_deadline_days >= 0'
    )
