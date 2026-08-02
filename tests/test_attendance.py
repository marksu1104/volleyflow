from datetime import date, datetime

from volleyflow.attendance import DropIn, WaitlistEntry, next_in_line
from volleyflow.players import Player
from volleyflow.schedule import Game

ALICE = Player(id=1, name="Alice")
BOB = Player(id=2, name="Bob")
CAROL = Player(id=3, name="Carol")
GAME = Game(id=1, date=date(2026, 8, 4))


def test_drop_in_is_active_when_not_cancelled():
    drop_in = DropIn(player=ALICE, game=GAME, signed_up_at=datetime(2026, 8, 1))

    assert drop_in.is_active is True


def test_drop_in_is_inactive_once_cancelled():
    drop_in = DropIn(
        player=ALICE,
        game=GAME,
        signed_up_at=datetime(2026, 8, 1),
        cancelled_at=datetime(2026, 8, 2),
    )

    assert drop_in.is_active is False


def test_next_in_line_returns_the_earliest_queued_entry():
    entries = [
        WaitlistEntry(player=CAROL, game=GAME, queued_at=datetime(2026, 8, 3)),
        WaitlistEntry(player=ALICE, game=GAME, queued_at=datetime(2026, 8, 1)),
        WaitlistEntry(player=BOB, game=GAME, queued_at=datetime(2026, 8, 2)),
    ]

    result = next_in_line(entries)

    assert result is not None
    assert result.player == ALICE


def test_next_in_line_returns_none_for_an_empty_waitlist():
    result = next_in_line([])

    assert result is None
