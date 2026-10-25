"""The per-game share and its rounding rule.

The only place a monetary amount is rounded; see docs/billing-rules.md
"Core formula" and "Why rounding happens exactly once".
"""

from collections.abc import Sequence
from decimal import ROUND_CEILING, Decimal


def share_per_game(
    total_venue_cost: Decimal, total_games: int, capacity: int
) -> Decimal:
    """One person's cost for one game, rounded up to a whole dollar.

    Split across `capacity` (the slots on court), not the current roster,
    so the price is fixed when the season is booked: a member joining or
    leaving changes only their own bill. An unfilled slot is money nobody
    pays. See docs/billing-rules.md "Who the cost is split between".
    """
    if total_games <= 0:
        raise ValueError("total_games must be positive")
    if capacity <= 0:
        raise ValueError("capacity must be positive")

    exact = total_venue_cost / (total_games * capacity)
    return exact.to_integral_value(rounding=ROUND_CEILING)


def member_season_fee(share: Decimal, billable_games: int) -> Decimal:
    """A member's total charge for the season.

    `billable_games` excludes CANCELLED_REFUNDED games. `share` is already
    whole dollars, so nothing is rounded here.
    """
    return share * billable_games


def shares_by_game(
    total_venue_cost: Decimal,
    air_conditioned: Sequence[bool],
    capacity: int,
    ac_surcharge: Decimal = Decimal("0"),
    venue_cost_deltas: Sequence[Decimal] | None = None,
) -> list[Decimal]:
    """Each game's share per person, in the order the games were given.

    A night with the air conditioning on costs the club `ac_surcharge`
    more, and a night at another venue costs its `venue_cost_deltas`
    entry more (or less). Both are taken out of `total_venue_cost`, which
    stays the authoritative figure, and added back on their own night:

        ac_total   = ac_surcharge x (games with the AC on)
        base_each  = (total_venue_cost - ac_total - sum(deltas)) / games
        share(g)   = ceil((base_each + AC if on + delta(g)) / capacity)

    With no surcharge and no deltas every game gets `share_per_game`.
    """
    total_games = len(air_conditioned)
    if total_games <= 0:
        raise ValueError("total_games must be positive")
    if capacity <= 0:
        raise ValueError("capacity must be positive")
    if ac_surcharge < 0:
        raise ValueError("ac_surcharge cannot be negative")

    deltas = (
        [Decimal("0")] * total_games
        if venue_cost_deltas is None
        else list(venue_cost_deltas)
    )
    if len(deltas) != total_games:
        raise ValueError("venue_cost_deltas needs one entry per game")

    ac_total = ac_surcharge * sum(1 for on in air_conditioned if on)
    delta_total = sum(deltas, Decimal("0"))
    base_total = total_venue_cost - ac_total - delta_total
    if base_total < 0:
        raise ValueError(
            "air conditioning and venue changes cost more than the whole "
            "venue bill — check ac_surcharge and venue_cost_deltas against "
            "total_venue_cost"
        )

    base_each = base_total / total_games
    return [
        (
            (base_each + (ac_surcharge if on else Decimal("0")) + delta) / capacity
        ).to_integral_value(rounding=ROUND_CEILING)
        for on, delta in zip(air_conditioned, deltas, strict=True)
    ]
