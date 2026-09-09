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
    air_conditioned: bool = False
    """Whether the air conditioning ran for this game.

    Set from a forecast when the season is created, and corrected on the
    day — whether the AC actually runs is decided that evening, not when
    the season is booked. Flipping it changes what the club pays the
    venue and therefore what every member owes; see
    docs/billing-rules.md "Air conditioning".
    """


@dataclass(frozen=True)
class Season:
    """A billing period: a fixed set of games, a total venue cost, a fixed
    member list, all decided when the season starts.
    """

    id: int
    total_venue_cost: Decimal
    games: tuple[Game, ...]
    members: tuple[Player, ...]
    ac_surcharge: Decimal = Decimal("0")
    """What one game's air conditioning adds to the venue bill.

    Every venue's AC pricing collapses to this one number: bundled
    venues charge 0, venues with a higher hourly rate when the AC is on
    charge the difference times the hours, venues billing a flat AC fee
    charge that. Zero — the default — makes every game cost the same and
    the whole calculation collapses back to what it was before this
    existed.
    """

    @property
    def air_conditioned_games(self) -> int:
        return sum(1 for game in self.games if game.air_conditioned)

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
