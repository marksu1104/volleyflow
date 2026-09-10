"""The per-game share calculation and its rounding rule.

The only place in the codebase where a monetary amount is rounded.
See docs/billing-rules.md "Core formula" and "Why rounding happens exactly
once" for the reasoning.
"""

from collections.abc import Sequence
from decimal import ROUND_CEILING, Decimal


def share_per_game(
    total_venue_cost: Decimal, total_games: int, capacity: int
) -> Decimal:
    """One person's cost for one game, rounded up to a whole dollar.

    `capacity` is the season's cap on how many play a game — not how many
    fixed members it currently has. That is the whole point: the price is
    settled when the season is booked and never moves again, so adding or
    dropping a member changes only that person's bill and nobody else's.
    Splitting by the current roster instead meant one person leaving an
    18-person season pushed everyone from $205 a night to $218, which is
    both a surprise and, once a drop-in has filled the empty slot and
    paid the same $205, an overcharge.

    The club therefore collects the full venue cost only when every slot
    is filled — by a member or by a drop-in, who pays this same figure.
    An unfilled slot is money the organizer doesn't collect, which is why
    capacity is theirs to set and worth setting honestly. Decided
    2026-09-10; see docs/billing-rules.md "Who the cost is split between".

    Every other amount in the system (season fees, drop-in fees, absence
    refunds) is a multiple of this value. Rounding happens here and nowhere
    else — see docs/billing-rules.md.
    """
    if total_games <= 0:
        raise ValueError("total_games must be positive")
    if capacity <= 0:
        raise ValueError("capacity must be positive")

    exact = total_venue_cost / (total_games * capacity)
    return exact.to_integral_value(rounding=ROUND_CEILING)


def member_season_fee(share: Decimal, billable_games: int) -> Decimal:
    """A member's total charge for the season.

    `billable_games` excludes CANCELLED_REFUNDED games — see
    docs/billing-rules.md "Game cancellation". No rounding here; `share`
    is already a whole dollar amount.
    """
    return share * billable_games


def shares_by_game(
    total_venue_cost: Decimal,
    air_conditioned: Sequence[bool],
    capacity: int,
    ac_surcharge: Decimal = Decimal("0"),
) -> list[Decimal]:
    """Each game's share per person, in the order the games were given.

    `capacity`, not the roster size — see share_per_game for why.

    Games don't all cost the same once air conditioning is in the
    picture: a night with the AC on costs the club `ac_surcharge` more
    than one without. Splitting the season total evenly across every
    game would make a drop-in on a cool December night subsidise the
    August ones, and would refund an absence from an expensive game at a
    cheap game's rate.

    `total_venue_cost` stays the authoritative figure — it is what the
    club actually pays, discounts and all — so the AC portion is taken
    out of it rather than added on top:

        ac_total   = ac_surcharge x (games with the AC on)
        base_each  = (total_venue_cost - ac_total) / total games
        share(g)   = ceil((base_each + ac_surcharge if g else base_each)
                          / capacity)

    With `ac_surcharge` at zero this is exactly `share_per_game` for
    every game, which is what makes the change invisible to every season
    booked before air conditioning was modelled.

    Rounding still happens once per share and nowhere else. The surplus
    it creates is the same surplus described in docs/billing-rules.md.
    """
    total_games = len(air_conditioned)
    if total_games <= 0:
        raise ValueError("total_games must be positive")
    if capacity <= 0:
        raise ValueError("capacity must be positive")
    if ac_surcharge < 0:
        raise ValueError("ac_surcharge cannot be negative")

    ac_total = ac_surcharge * sum(1 for on in air_conditioned if on)
    base_total = total_venue_cost - ac_total
    if base_total < 0:
        raise ValueError(
            "air conditioning costs more than the whole venue bill — "
            "check ac_surcharge against total_venue_cost"
        )

    base_each = base_total / total_games
    return [
        (
            (base_each + (ac_surcharge if on else Decimal("0"))) / capacity
        ).to_integral_value(rounding=ROUND_CEILING)
        for on in air_conditioned
    ]
