"""Season and Game: what's on the calendar, and each game's status."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import Enum

from volleyflow.players import Player


class GameStatus(Enum):
    SCHEDULED = "scheduled"
    CANCELLED_UNREFUNDED = "cancelled_unrefunded"
    CANCELLED_REFUNDED = "cancelled_refunded"


@dataclass(frozen=True)
class Game:
    id: int
    date: date
    status: GameStatus = GameStatus.SCHEDULED


@dataclass(frozen=True)
class Season:
    """A billing period: a fixed set of games, a total venue cost, a fixed
    member list, all decided when the season starts.
    """

    id: int
    total_venue_cost: Decimal
    games: tuple[Game, ...]
    members: tuple[Player, ...]

    @property
    def total_games(self) -> int:
        return len(self.games)

    @property
    def member_count(self) -> int:
        return len(self.members)

    @property
    def billable_games(self) -> int:
        """Games still charged for — everything except CANCELLED_REFUNDED.

        See docs/billing-rules.md "Game cancellation".
        """
        return sum(
            1 for game in self.games if game.status != GameStatus.CANCELLED_REFUNDED
        )
