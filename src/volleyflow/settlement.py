"""The season-end settlement engine: what each player owes or is owed.

Combines pricing, schedule, and attendance into the two numbers that
matter: a member's season fee net of refunds, and a drop-in's charge.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from volleyflow.attendance import Absence, DropIn
from volleyflow.players import Player
from volleyflow.pricing import member_season_fee, share_per_game
from volleyflow.schedule import Game, GameStatus, Season


def covered_absences(
    game: Game, absences: Sequence[Absence], drop_ins: Sequence[DropIn]
) -> list[Absence]:
    """Absences for this game that get refunded.

    A drop-in directly nominated as someone's substitute (`covers` set)
    always refunds that specific absence, regardless of FIFO order.
    Everyone else — absences with no substitute, drop-ins with nothing
    to cover — are each sorted earliest first and paired off one-to-one;
    a leftover drop-in is just filling open capacity, not covering
    anyone. See docs/billing-rules.md "FIFO when coverage falls short".
    """
    game_absences = [a for a in absences if a.game == game and a.is_active]
    game_drop_ins = [d for d in drop_ins if d.game == game and d.is_active]

    substituted = {
        d.covers
        for d in game_drop_ins
        if d.covers is not None and d.covers.game == game
    }
    fifo_absences = sorted(
        (a for a in game_absences if a not in substituted), key=lambda a: a.recorded_at
    )
    fifo_drop_ins = [d for d in game_drop_ins if d.covers is None]

    return list(substituted & set(game_absences)) + fifo_absences[: len(fifo_drop_ins)]


@dataclass(frozen=True)
class MemberSettlement:
    player: Player
    season_fee: Decimal
    refund: Decimal

    @property
    def net(self) -> Decimal:
        """Positive: the organizer owes the member. Negative: the member
        owes the organizer. Same convention as ledger.balance().
        """
        return self.refund - self.season_fee


def settle_member(
    player: Player,
    season: Season,
    absences: Sequence[Absence],
    drop_ins: Sequence[DropIn],
) -> MemberSettlement:
    """A member's season fee and refund, ignoring CANCELLED_REFUNDED games."""
    share = share_per_game(
        season.total_venue_cost, season.total_games, season.member_count
    )
    fee = member_season_fee(share, season.billable_games)

    billable = (g for g in season.games if g.status != GameStatus.CANCELLED_REFUNDED)
    refunded_games = sum(
        1
        for game in billable
        for absence in covered_absences(game, absences, drop_ins)
        if absence.player == player
    )

    return MemberSettlement(
        player=player, season_fee=fee, refund=share * refunded_games
    )


def settle_drop_in(drop_in: DropIn, season: Season) -> Decimal:
    """A drop-in's charge for the game they signed up for, 0 if cancelled."""
    if not drop_in.is_active:
        return Decimal("0")
    return share_per_game(
        season.total_venue_cost, season.total_games, season.member_count
    )
