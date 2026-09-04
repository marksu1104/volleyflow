"""Absence, DropIn, and WaitlistEntry: who's in, who's out, who's next.

Most drop-ins aren't tied to any particular absence — which one "covers"
which is computed at settlement time by pairing both lists in timestamp
order. See settlement.py and docs/billing-rules.md "FIFO when coverage
falls short". A drop-in can also be *directly* nominated as a member's
own substitute (a "代打") instead of coming from that FIFO pool — see
`DropIn.covers`.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from volleyflow.players import Player
from volleyflow.schedule import Game


@dataclass(frozen=True)
class Absence:
    """A member skipping a game they're otherwise expected to attend."""

    player: Player
    game: Game
    recorded_at: datetime
    cancelled_at: datetime | None = None

    @property
    def is_active(self) -> bool:
        return self.cancelled_at is None


@dataclass(frozen=True)
class DropIn:
    """A non-member signed up for a single game."""

    player: Player
    game: Game
    signed_up_at: datetime
    cancelled_at: datetime | None = None
    covers: Absence | None = None
    """Set when a member personally arranged this drop-in as their own
    substitute for a specific absence, instead of it coming from the
    general FIFO waitlist pool. Filling your own vacated slot with
    someone you picked isn't competing with strangers queued for open
    capacity, so this bypasses the waitlist rather than jumping it."""

    @property
    def is_active(self) -> bool:
        return self.cancelled_at is None


@dataclass(frozen=True)
class WaitlistEntry:
    """A DropIn signup that didn't get a slot yet, in order."""

    player: Player
    game: Game
    queued_at: datetime


def next_in_line(entries: Sequence[WaitlistEntry]) -> WaitlistEntry | None:
    """The earliest-queued entry, or None if the waitlist is empty."""
    if not entries:
        return None
    return min(entries, key=lambda entry: entry.queued_at)
