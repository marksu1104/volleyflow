"""record server errors and important operations

Revision ID: b5d2f8c1e930
Revises: a7c3e9d15b42
Create Date: 2026-10-22 09:00:00.000000

Two append-only tables for the developer's overview:

- `error_events`: a request the server failed on — when, which call,
  from which screen, and the line of this project's code that raised.
  Until now that lived only in Render's log.
- `audit_events`: who did what to whose money or membership — payments,
  removals, merges, settlements, cancellations, venue-cost and
  air-conditioning changes. Names are copied in, not foreign keys, so
  a record outlives merges and deleted clubs.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b5d2f8c1e930'
down_revision: Union[str, Sequence[str], None] = 'a7c3e9d15b42'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'error_events',
        sa.Column('id', sa.Integer(), sa.Identity(), primary_key=True),
        sa.Column('occurred_at', sa.DateTime(), nullable=False),
        sa.Column('method', sa.String(), nullable=False),
        sa.Column('path', sa.String(), nullable=False),
        sa.Column('page', sa.String(), nullable=True),
        sa.Column('error_type', sa.String(), nullable=False),
        sa.Column('message', sa.String(), nullable=False),
        sa.Column('location', sa.String(), nullable=False),
    )
    op.create_index('ix_error_events_occurred_at', 'error_events', ['occurred_at'])
    op.create_table(
        'audit_events',
        sa.Column('id', sa.Integer(), sa.Identity(), primary_key=True),
        sa.Column('occurred_at', sa.DateTime(), nullable=False),
        sa.Column('action', sa.String(), nullable=False),
        sa.Column('actor_id', sa.Integer(), nullable=True),
        sa.Column('actor_name', sa.String(), nullable=False),
        sa.Column('club_id', sa.Integer(), nullable=True),
        sa.Column('club_name', sa.String(), nullable=True),
        sa.Column('subject_name', sa.String(), nullable=True),
        sa.Column('amount', sa.Numeric(10, 0), nullable=True),
        sa.Column('detail', sa.String(), nullable=True),
    )
    op.create_index('ix_audit_events_occurred_at', 'audit_events', ['occurred_at'])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_audit_events_occurred_at', table_name='audit_events')
    op.drop_table('audit_events')
    op.drop_index('ix_error_events_occurred_at', table_name='error_events')
    op.drop_table('error_events')
