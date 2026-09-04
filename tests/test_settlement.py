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
