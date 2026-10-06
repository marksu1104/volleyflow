"""one signup table

Revision ID: c9d1e5f2a374
Revises: b7e4c2a9d816
Create Date: 2026-10-07 01:00:00.000000

Playing and waiting become one table. `drop_ins` gains a `status`
('playing' or 'queued'), `from_waitlist_at` is renamed `queued_at` — it
always meant "when they joined the queue" — and every row of
`waitlist_entries` moves in as a queued signup, keeping its time and its
bringer. Moving somebody between court and queue then changes one
column instead of copying a row between tables, which is where two real
bugs came from (see models.DropInRow).

Refuses, rather than guesses, when the same person is both on court and
queued for one game: the old tables allowed it only through a bug, and
which of the two is true is the organizer's call.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c9d1e5f2a374'
down_revision: Union[str, Sequence[str], None] = 'b7e4c2a9d816'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    conn = op.get_bind()
    both = conn.execute(
        sa.text(
            "SELECT w.player_id, w.game_id FROM waitlist_entries w "
            "JOIN drop_ins d ON d.player_id = w.player_id AND d.game_id = w.game_id "
            "WHERE d.cancelled_at IS NULL"
        )
    ).fetchall()
    if both:
        raise RuntimeError(
            f"On court and queued for the same game at once: {both}. "
            "Resolve these by hand before merging the tables."
        )

    op.add_column(
        'drop_ins',
        sa.Column('status', sa.String(), nullable=False, server_default='playing'),
    )
    op.create_check_constraint(
        'ck_drop_ins_status', 'drop_ins', "status IN ('playing', 'queued')"
    )
    op.alter_column('drop_ins', 'from_waitlist_at', new_column_name='queued_at')
    op.execute(
        "INSERT INTO drop_ins "
        "(player_id, game_id, signed_up_at, queued_at, brought_by_player_id, status) "
        "SELECT player_id, game_id, queued_at, queued_at, brought_by_player_id, 'queued' "
        "FROM waitlist_entries"
    )
    op.drop_table('waitlist_entries')


def downgrade() -> None:
    """Downgrade schema."""
    op.create_table(
        'waitlist_entries',
        sa.Column('id', sa.Integer(), sa.Identity(), primary_key=True),
        sa.Column('player_id', sa.Integer(), sa.ForeignKey('players.id'), nullable=False),
        sa.Column('game_id', sa.Integer(), sa.ForeignKey('games.id'), nullable=False),
        sa.Column('queued_at', sa.DateTime(), nullable=False),
        sa.Column(
            'brought_by_player_id', sa.Integer(), sa.ForeignKey('players.id'), nullable=True
        ),
        sa.UniqueConstraint('player_id', 'game_id', name='uq_waitlist_player_game'),
    )
    op.create_index('ix_waitlist_entries_game_id', 'waitlist_entries', ['game_id'])
    op.execute(
        "INSERT INTO waitlist_entries (player_id, game_id, queued_at, brought_by_player_id) "
        "SELECT player_id, game_id, queued_at, brought_by_player_id FROM drop_ins "
        "WHERE status = 'queued' AND cancelled_at IS NULL"
    )
    op.execute("DELETE FROM drop_ins WHERE status = 'queued'")
    op.alter_column('drop_ins', 'queued_at', new_column_name='from_waitlist_at')
    op.drop_constraint('ck_drop_ins_status', 'drop_ins', type_='check')
    op.drop_column('drop_ins', 'status')
