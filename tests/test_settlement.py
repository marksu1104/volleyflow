from datetime import date, datetime
from decimal import Decimal

from volleyflow.attendance import Absence, DropIn
from volleyflow.players import Player
from volleyflow.schedule import Game, GameStatus, Season
from volleyflow.settlement import settle_drop_in, settle_member

ALICE = Player(id=1, name="Alice")
BOB = Player(id=2, name="Bob")
CAROL = Player(id=3, name="Carol")
MEMBERS = (
    ALICE,
    BOB,
    Player(id=4, name="Dave"),
    Player(id=5, name="Eve"),
    Player(id=6, name="Frank"),
)

GAME1 = Game(id=1, date=date(2026, 8, 4))
GAME2 = Game(id=2, date=date(2026, 8, 11))


def _season(games: tuple[Game, ...] = (GAME1, GAME2)) -> Season:
    # total_venue_cost=10000, 8 games, 5 members -> share_per_game = 250
    all_games = games + tuple(
        Game(id=100 + i, date=date(2026, 9, i + 1)) for i in range(8 - len(games))
    )
    return Season(
        id=1,
        total_venue_cost=Decimal("10000"),
        games=all_games,
        members=MEMBERS,
    )


def test_settle_member_baseline_charges_full_season_fee_with_no_refund():
    season = _season()

    result = settle_member(ALICE, season, absences=[], drop_ins=[])

    assert result.season_fee == Decimal("2000")
    assert result.refund == Decimal("0")
    assert result.net == Decimal("-2000")


def test_settle_member_refunds_a_single_absence_covered_by_a_drop_in():
    season = _season()
    absences = [Absence(ALICE, GAME1, recorded_at=datetime(2026, 8, 1))]
    drop_ins = [DropIn(CAROL, GAME1, signed_up_at=datetime(2026, 8, 2))]

    result = settle_member(ALICE, season, absences, drop_ins)

    assert result.refund == Decimal("250")
    assert result.net == Decimal("-1750")


def test_settle_member_does_not_refund_an_uncovered_absence():
    season = _season()
    absences = [Absence(ALICE, GAME1, recorded_at=datetime(2026, 8, 1))]

    result = settle_member(ALICE, season, absences, drop_ins=[])

    assert result.refund == Decimal("0")


def test_settle_member_refunds_the_same_player_absent_multiple_times():
    season = _season()
    absences = [
        Absence(ALICE, GAME1, recorded_at=datetime(2026, 8, 1)),
        Absence(ALICE, GAME2, recorded_at=datetime(2026, 8, 8)),
    ]
    drop_ins = [
        DropIn(CAROL, GAME1, signed_up_at=datetime(2026, 8, 2)),
        DropIn(CAROL, GAME2, signed_up_at=datetime(2026, 8, 9)),
    ]

    result = settle_member(ALICE, season, absences, drop_ins)

    assert result.refund == Decimal("500")


def test_settle_member_fifo_refunds_the_earlier_absence_first():
    season = _season()
    absences = [
        Absence(BOB, GAME1, recorded_at=datetime(2026, 8, 2)),
        Absence(ALICE, GAME1, recorded_at=datetime(2026, 8, 1)),
    ]
    drop_ins = [DropIn(CAROL, GAME1, signed_up_at=datetime(2026, 8, 3))]

    alice_result = settle_member(ALICE, season, absences, drop_ins)
    bob_result = settle_member(BOB, season, absences, drop_ins)

    assert alice_result.refund == Decimal("250")
    assert bob_result.refund == Decimal("0")


def test_settle_member_ignores_absences_on_a_cancelled_refunded_game():
    season = _season(games=(GAME1, GAME2))
    refunded_game = Game(
        id=2, date=date(2026, 8, 11), status=GameStatus.CANCELLED_REFUNDED
    )
    season = Season(
        id=season.id,
        total_venue_cost=season.total_venue_cost,
        games=(GAME1, refunded_game) + season.games[2:],
        members=season.members,
    )
    absences = [Absence(ALICE, refunded_game, recorded_at=datetime(2026, 8, 1))]
    drop_ins = [DropIn(CAROL, refunded_game, signed_up_at=datetime(2026, 8, 2))]

    result = settle_member(ALICE, season, absences, drop_ins)

    assert result.refund == Decimal("0")
    assert result.season_fee == Decimal("1750")  # 250 * 7 billable games


def test_settle_member_unrefunded_cancellation_bills_normally():
    unrefunded_game = Game(
        id=2, date=date(2026, 8, 11), status=GameStatus.CANCELLED_UNREFUNDED
    )
    season = _season(games=(GAME1, unrefunded_game))
    absences = [Absence(ALICE, unrefunded_game, recorded_at=datetime(2026, 8, 1))]

    result = settle_member(ALICE, season, absences, drop_ins=[])

    assert result.season_fee == Decimal("2000")  # still billable, no special case
    assert result.refund == Decimal("0")  # nobody attends, so nobody covers it


def test_settle_drop_in_charges_the_per_game_share():
    season = _season()
    drop_in = DropIn(CAROL, GAME1, signed_up_at=datetime(2026, 8, 2))

    result = settle_drop_in(drop_in, season)

    assert result == Decimal("250")


def test_settle_drop_in_charges_nothing_once_cancelled():
    season = _season()
    drop_in = DropIn(
        CAROL,
        GAME1,
        signed_up_at=datetime(2026, 8, 2),
        cancelled_at=datetime(2026, 8, 3),
    )

    result = settle_drop_in(drop_in, season)

    assert result == Decimal("0")


def test_settle_member_does_not_count_a_cancelled_drop_in_as_coverage():
    season = _season()
    absences = [Absence(ALICE, GAME1, recorded_at=datetime(2026, 8, 1))]
    drop_ins = [
        DropIn(
            CAROL,
            GAME1,
            signed_up_at=datetime(2026, 8, 2),
            cancelled_at=datetime(2026, 8, 3),
        )
    ]

    result = settle_member(ALICE, season, absences, drop_ins)

    assert result.refund == Decimal("0")


def test_settle_member_does_not_refund_a_cancelled_absence():
    season = _season()
    absences = [
        Absence(
            ALICE,
            GAME1,
            recorded_at=datetime(2026, 8, 1),
            cancelled_at=datetime(2026, 8, 2),
        )
    ]
    drop_ins = [DropIn(CAROL, GAME1, signed_up_at=datetime(2026, 8, 2))]

    result = settle_member(ALICE, season, absences, drop_ins)

    assert result.refund == Decimal("0")


def test_settle_member_refunds_a_substitute_regardless_of_fifo_order():
    """Bob's absence is recorded first, so plain FIFO would refund him —
    but Alice arranged her own named substitute, so her later absence is
    the one that gets refunded, not Bob's.
    """
    season = _season()
    alice_absence = Absence(ALICE, GAME1, recorded_at=datetime(2026, 8, 2))
    bob_absence = Absence(BOB, GAME1, recorded_at=datetime(2026, 8, 1))
    absences = [alice_absence, bob_absence]
    drop_ins = [
        DropIn(CAROL, GAME1, signed_up_at=datetime(2026, 8, 3), covers=alice_absence)
    ]

    alice_result = settle_member(ALICE, season, absences, drop_ins)
    bob_result = settle_member(BOB, season, absences, drop_ins)

    assert alice_result.refund == Decimal("250")
    assert bob_result.refund == Decimal("0")


def test_settle_member_substitute_does_not_consume_a_fifo_slot():
    """Alice's substitute is a separate arrangement from the general
    waitlist pool — Bob's uncovered absence still gets FIFO-matched to
    the one general drop-in, unaffected by Alice's substitute existing.
    """
    season = _season()
    alice_absence = Absence(ALICE, GAME1, recorded_at=datetime(2026, 8, 1))
    bob_absence = Absence(BOB, GAME1, recorded_at=datetime(2026, 8, 2))
    absences = [alice_absence, bob_absence]
    drop_ins = [
        DropIn(CAROL, GAME1, signed_up_at=datetime(2026, 8, 3), covers=alice_absence),
        DropIn(Player(id=7, name="Grace"), GAME1, signed_up_at=datetime(2026, 8, 4)),
    ]

    alice_result = settle_member(ALICE, season, absences, drop_ins)
    bob_result = settle_member(BOB, season, absences, drop_ins)

    assert alice_result.refund == Decimal("250")
    assert bob_result.refund == Decimal("250")


def _cooled_season() -> Season:
    """Two games, one with the air conditioning on, 5 members.

    base = (10000 - 1000) / 2 = 4500 -> 900 each
    cooled = 4500 + 1000 = 5500   -> 1100 each
    """
    return Season(
        id=1,
        total_venue_cost=Decimal("10000"),
        games=(
            Game(id=1, date=date(2026, 8, 4), air_conditioned=True),
            Game(id=2, date=date(2026, 8, 11)),
        ),
        members=MEMBERS,
        ac_surcharge=Decimal("1000"),
    )


def test_a_season_fee_is_the_sum_of_its_games_not_a_share_times_a_count():
    # With air conditioning the games differ, so "share x games" is no
    # longer the same number as "add the games up".
    result = settle_member(ALICE, _cooled_season(), absences=[], drop_ins=[])

    assert result.season_fee == Decimal("2000")  # 1100 + 900


def test_a_covered_absence_refunds_that_game_s_own_price():
    # Missing the cooled night is worth more than missing the cool one —
    # refunding both at one flat rate would quietly move money between
    # members.
    season = _cooled_season()
    cooled, plain = season.games

    cooled_absence = Absence(
        player=ALICE, game=cooled, recorded_at=datetime(2026, 7, 1)
    )
    plain_absence = Absence(player=BOB, game=plain, recorded_at=datetime(2026, 7, 1))
    drop_ins = [
        DropIn(
            player=Player(id=91, name="Guest 1"),
            game=cooled,
            signed_up_at=datetime(2026, 7, 2),
        ),
        DropIn(
            player=Player(id=92, name="Guest 2"),
            game=plain,
            signed_up_at=datetime(2026, 7, 2),
        ),
    ]

    alice = settle_member(
        ALICE, season, absences=[cooled_absence, plain_absence], drop_ins=drop_ins
    )
    bob = settle_member(
        BOB, season, absences=[cooled_absence, plain_absence], drop_ins=drop_ins
    )

    assert alice.refund == Decimal("1100")
    assert bob.refund == Decimal("900")


def test_a_drop_in_pays_the_price_of_the_night_they_turn_up_to():
    season = _cooled_season()
    cooled, plain = season.games

    on_a_hot_night = DropIn(
        player=Player(id=93, name="Guest"),
        game=cooled,
        signed_up_at=datetime(2026, 7, 2),
    )
    on_a_cool_night = DropIn(
        player=Player(id=93, name="Guest"),
        game=plain,
        signed_up_at=datetime(2026, 7, 2),
    )

    assert settle_drop_in(on_a_hot_night, season) == Decimal("1100")
    assert settle_drop_in(on_a_cool_night, season) == Decimal("900")


def test_turning_the_air_conditioning_off_lowers_only_that_game():
    # What the organizer does on the day when the forecast was wrong.
    # The club pays the venue ac_surcharge less, so the season total
    # drops with it — and only that night gets cheaper.
    before = _cooled_season()
    after = Season(
        id=1,
        total_venue_cost=Decimal("9000"),
        games=(
            Game(id=1, date=date(2026, 8, 4)),
            Game(id=2, date=date(2026, 8, 11)),
        ),
        members=MEMBERS,
        ac_surcharge=Decimal("1000"),
    )

    assert settle_member(ALICE, before, [], []).season_fee == Decimal("2000")
    assert settle_member(ALICE, after, [], []).season_fee == Decimal("1800")
