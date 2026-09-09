"""The season-end settlement engine: what each player owes or is owed.

Combines pricing, schedule, and attendance into the two numbers that
matter: a member's season fee net of refunds, and a drop-in's charge.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from volleyflow.attendance import Absence, DropIn
from volleyflow.players import Player
from volleyflow.pricing import shares_by_game
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


def season_shares(season: Season) -> dict[int, Decimal]:
    """Each game's per-member share, keyed by game id.

    One place computes this, and everything that touches money for a
    single game goes through it — a member's fee, a drop-in's charge, an
    absence refund. Games can cost different amounts once air
    conditioning is modelled, so "the season's share" is no longer a
    single number; see pricing.shares_by_game.
    """
    shares = shares_by_game(
        season.total_venue_cost,
        [game.air_conditioned for game in season.games],
        season.member_count,
        season.ac_surcharge,
    )
    return {game.id: share for game, share in zip(season.games, shares, strict=True)}


def settle_member(
    player: Player,
    season: Season,
    absences: Sequence[Absence],
    drop_ins: Sequence[DropIn],
) -> MemberSettlement:
    """A member's season fee and refund, ignoring CANCELLED_REFUNDED games."""
    shares = season_shares(season)
    billable = [g for g in season.games if g.status != GameStatus.CANCELLED_REFUNDED]

    # The fee is the sum of the games they're being charged for, not a
    # share times a count: with air conditioning the games differ, and a
    # season where half the nights are cooled costs more than twice the
    # cheap half.
    fee = sum((shares[g.id] for g in billable), Decimal("0"))

    refund = sum(
        (
            shares[game.id]
            for game in billable
            for absence in covered_absences(game, absences, drop_ins)
            if absence.player == player
        ),
        Decimal("0"),
    )

    return MemberSettlement(player=player, season_fee=fee, refund=refund)


def settle_drop_in(drop_in: DropIn, season: Season) -> Decimal:
    """A drop-in's charge for the game they signed up for, 0 if cancelled.

    Priced at that game's own share: a night with the air conditioning
    on costs the club more, and a drop-in on a cool night should not be
    subsidising one on a hot one.
    """
    if not drop_in.is_active:
        return Decimal("0")
    return season_shares(season)[drop_in.game.id]
