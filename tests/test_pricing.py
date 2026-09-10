from decimal import Decimal

import pytest

from volleyflow.pricing import member_season_fee, share_per_game, shares_by_game


def test_share_per_game_divides_evenly_when_the_split_is_clean():
    total_venue_cost = Decimal("10000")

    result = share_per_game(total_venue_cost, total_games=8, capacity=5)

    assert result == Decimal("250")


def test_share_per_game_rounds_up_when_the_split_does_not_divide_evenly():
    total_venue_cost = Decimal("10000")

    result = share_per_game(total_venue_cost, total_games=7, capacity=5)

    assert result == Decimal("286")


def test_share_per_game_returns_a_decimal():
    result = share_per_game(Decimal("10000"), total_games=7, capacity=5)

    assert isinstance(result, Decimal)


def test_share_per_game_rejects_zero_games():
    with pytest.raises(ValueError):
        share_per_game(Decimal("10000"), total_games=0, capacity=5)


def test_share_per_game_rejects_zero_capacity():
    with pytest.raises(ValueError):
        share_per_game(Decimal("10000"), total_games=8, capacity=0)


def test_member_season_fee_multiplies_share_by_billable_games():
    result = member_season_fee(Decimal("286"), billable_games=7)

    assert result == Decimal("2002")


def test_member_season_fee_is_zero_when_no_games_are_billable():
    result = member_season_fee(Decimal("286"), billable_games=0)

    assert result == Decimal("0")


def test_shares_by_game_without_air_conditioning_matches_the_flat_split() -> None:
    # The whole point of the default: a season booked before air
    # conditioning was modelled must be charged exactly as it was.
    flat = share_per_game(Decimal("54990"), 13, 18)

    shares = shares_by_game(Decimal("54990"), [False] * 13, 18)

    assert shares == [flat] * 13
    assert flat == Decimal("235")


def test_a_cooled_game_costs_more_than_a_cool_one() -> None:
    # This club's real season, checked against the venue's own invoice:
    # 13 games, 8 with the AC on, 52290 paid after a 14305 discount, and
    # 540 for one night's air conditioning (180/hour x 3 hours, quoted
    # separately and *not* covered by the discount).
    shares = shares_by_game(
        Decimal("52290"), [True] * 8 + [False] * 5, 18, Decimal("540")
    )

    assert shares[:8] == [Decimal("235")] * 8
    assert shares[8:] == [Decimal("205")] * 5


def test_the_shares_still_cover_what_the_club_actually_pays() -> None:
    # The surplus from rounding each share up is expected — see
    # docs/billing-rules.md — but it must never go the other way. On
    # these particular numbers it happens to divide exactly, which is
    # worth pinning: it is what the organizer checked by hand.
    cost = Decimal("52290")
    shares = shares_by_game(cost, [True] * 8 + [False] * 5, 18, Decimal("540"))

    collected = sum(shares) * 18

    assert collected == cost
    assert collected >= cost


def test_the_air_conditioning_bill_cannot_exceed_the_venue_bill() -> None:
    # A mistyped surcharge would otherwise produce a negative base cost
    # and silently charge less for the games that had no AC.
    with pytest.raises(ValueError, match="more than the whole venue bill"):
        shares_by_game(Decimal("1000"), [True, True], 5, Decimal("600"))


def test_a_negative_surcharge_is_refused() -> None:
    with pytest.raises(ValueError, match="cannot be negative"):
        shares_by_game(Decimal("1000"), [True], 5, Decimal("-10"))


def test_shares_by_game_rejects_an_empty_season() -> None:
    with pytest.raises(ValueError, match="total_games must be positive"):
        shares_by_game(Decimal("1000"), [], 5)


def test_shares_by_game_rejects_zero_capacity() -> None:
    with pytest.raises(ValueError, match="capacity must be positive"):
        shares_by_game(Decimal("1000"), [True], 0)
