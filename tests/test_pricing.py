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


def test_a_night_at_a_pricier_court_leaves_every_other_night_alone() -> None:
    # The property that makes a per-game delta worth having, on this
    # club's own season: one uncooled night moves to a court costing 800
    # more, so the club transfers 800 more — and nobody else's nights
    # change price at all. See docs/billing-rules.md, "A different venue
    # for one night".
    deltas = [Decimal("0")] * 8 + [Decimal("800")] + [Decimal("0")] * 4

    shares = shares_by_game(
        Decimal("53090"), [True] * 8 + [False] * 5, 18, Decimal("540"), deltas
    )

    assert shares[:8] == [Decimal("235")] * 8, "the cooled nights do not move"
    assert shares[9:] == [Decimal("205")] * 4, "nor do the other plain ones"
    assert shares[8] == Decimal("250"), "only the night that cost more"


def test_a_delta_still_covers_what_the_club_actually_pays() -> None:
    # The check that says the model is right: whatever the deltas do, the
    # shares have to add back up to what was transferred.
    cost = Decimal("53090")
    deltas = [Decimal("0")] * 8 + [Decimal("800")] + [Decimal("0")] * 4

    shares = shares_by_game(cost, [True] * 8 + [False] * 5, 18, Decimal("540"), deltas)

    assert sum(shares) * 18 >= cost


def test_no_deltas_prices_a_season_exactly_as_before() -> None:
    # The invisibility guarantee, same as ac_surcharge's default: every
    # season booked before this existed must be charged identically.
    without = shares_by_game(
        Decimal("52290"), [True] * 8 + [False] * 5, 18, Decimal("540")
    )

    with_zeros = shares_by_game(
        Decimal("52290"),
        [True] * 8 + [False] * 5,
        18,
        Decimal("540"),
        [Decimal("0")] * 13,
    )

    assert with_zeros == without


def test_a_cheaper_court_is_allowed() -> None:
    # A negative delta is a real case — a night at a cheaper hall — and
    # only the aggregate is guarded, so it simply nets off.
    shares = shares_by_game(
        Decimal("900"),
        [False] * 3,
        5,
        Decimal("0"),
        [Decimal("-60")] + [Decimal("0")] * 2,
    )

    assert shares[0] < shares[1]
    assert shares[1] == shares[2]


def test_a_delta_bigger_than_the_whole_bill_is_refused() -> None:
    with pytest.raises(ValueError, match="more than the whole venue bill"):
        shares_by_game(
            Decimal("1000"),
            [False, False],
            5,
            Decimal("0"),
            [Decimal("2000"), Decimal("0")],
        )


def test_one_delta_per_game_or_none_at_all() -> None:
    # A list that does not line up with the games would silently price
    # the wrong nights, so it is refused rather than padded.
    with pytest.raises(ValueError, match="one entry per game"):
        shares_by_game(Decimal("1000"), [False, False], 5, Decimal("0"), [Decimal("0")])
