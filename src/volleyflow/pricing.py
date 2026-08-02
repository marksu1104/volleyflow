"""The per-game share calculation and its rounding rule.

The only place in the codebase where a monetary amount is rounded.
See docs/billing-rules.md "Core formula" and "Why rounding happens exactly
once" for the reasoning.
"""

from decimal import ROUND_CEILING, Decimal


def share_per_game(
    total_venue_cost: Decimal, total_games: int, member_count: int
) -> Decimal:
    """Each member's cost for one game, rounded up to a whole dollar.

    Every other amount in the system (season fees, drop-in fees, absence
    refunds) is a multiple of this value. Rounding happens here and nowhere
    else — see docs/billing-rules.md.
    """
    if total_games <= 0:
        raise ValueError("total_games must be positive")
    if member_count <= 0:
        raise ValueError("member_count must be positive")

    exact = total_venue_cost / (total_games * member_count)
    return exact.to_integral_value(rounding=ROUND_CEILING)


def member_season_fee(share: Decimal, billable_games: int) -> Decimal:
    """A member's total charge for the season.

    `billable_games` excludes CANCELLED_REFUNDED games — see
    docs/billing-rules.md "Game cancellation". No rounding here; `share`
    is already a whole dollar amount.
    """
    return share * billable_games
