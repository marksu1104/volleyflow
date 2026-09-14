"""Store problem reports instead of pushing them over LINE

Revision ID: 5c2e9a1d7f40
Revises: badd3a1dbb41
Create Date: 2026-09-15 01:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '5c2e9a1d7f40'
down_revision: Union[str, Sequence[str], None] = 'badd3a1dbb41'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TEXT_COLUMNS = ("message", "reporter", "club", "page", "user_agent")


def upgrade() -> None:
    """Upgrade schema."""
    # All nullable, and no backfill. Rows already here are screenshots
    # kept only long enough for LINE to fetch them, with no words attached:
    # they stay, are never listed, and age out on their own.
    for name in _TEXT_COLUMNS:
        op.add_column('problem_reports', sa.Column(name, sa.String(), nullable=True))
    op.add_column('problem_reports', sa.Column('read_at', sa.DateTime(), nullable=True))
    # A report without a screenshot is now the ordinary case.
    op.alter_column('problem_reports', 'image', existing_type=sa.LargeBinary(), nullable=True)
    op.alter_column('problem_reports', 'content_type', existing_type=sa.String(), nullable=True)


def downgrade() -> None:
    """Downgrade schema."""
    # The old table could only hold screenshots; a report of words alone
    # has nowhere to go and would block NOT NULL coming back.
    op.execute("DELETE FROM problem_reports WHERE image IS NULL OR content_type IS NULL")
    op.alter_column('problem_reports', 'content_type', existing_type=sa.String(), nullable=False)
    op.alter_column('problem_reports', 'image', existing_type=sa.LargeBinary(), nullable=False)
    op.drop_column('problem_reports', 'read_at')
    for name in reversed(_TEXT_COLUMNS):
        op.drop_column('problem_reports', name)
