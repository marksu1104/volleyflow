from decimal import Decimal

import pytest

from volleyflow.pricing import member_season_fee, share_per_game


def test_share_per_game_divides_evenly_when_the_split_is_clean():
    total_venue_cost = Decimal("10000")

    result = share_per_game(total_venue_cost, total_games=8, member_count=5)

    assert result == Decimal("250")


def test_share_per_game_rounds_up_when_the_split_does_not_divide_evenly():
    total_venue_cost = Decimal("10000")

    result = share_per_game(total_venue_cost, total_games=7, member_count=5)

    assert result == Decimal("286")


def test_share_per_game_returns_a_decimal():
    result = share_per_game(Decimal("10000"), total_games=7, member_count=5)

    assert isinstance(result, Decimal)


def test_share_per_game_rejects_zero_games():
    with pytest.raises(ValueError):
        share_per_game(Decimal("10000"), total_games=0, member_count=5)


def test_share_per_game_rejects_zero_members():
    with pytest.raises(ValueError):
        share_per_game(Decimal("10000"), total_games=8, member_count=0)


def test_member_season_fee_multiplies_share_by_billable_games():
    result = member_season_fee(Decimal("286"), billable_games=7)

    assert result == Decimal("2002")


def test_member_season_fee_is_zero_when_no_games_are_billable():
    result = member_season_fee(Decimal("286"), billable_games=0)

    assert result == Decimal("0")
