from datetime import date
from decimal import Decimal

from volleyflow.players import Player
from volleyflow.schedule import Game, GameStatus, Season

ALICE = Player(id=1, name="Alice")
BOB = Player(id=2, name="Bob")


def _season(games: tuple[Game, ...]) -> Season:
    return Season(
        id=1,
        total_venue_cost=Decimal("10000"),
        games=games,
        members=(ALICE, BOB),
    )


def test_season_total_games_counts_every_game_regardless_of_status():
    season = _season(
        (
            Game(id=1, date=date(2026, 8, 4)),
            Game(id=2, date=date(2026, 8, 11), status=GameStatus.CANCELLED_REFUNDED),
        )
    )

    assert season.total_games == 2


def test_season_member_count_counts_fixed_members():
    season = _season((Game(id=1, date=date(2026, 8, 4)),))

    assert season.member_count == 2


def test_season_billable_games_excludes_cancelled_refunded():
    season = _season(
        (
            Game(id=1, date=date(2026, 8, 4)),
            Game(id=2, date=date(2026, 8, 11), status=GameStatus.CANCELLED_REFUNDED),
        )
    )

    assert season.billable_games == 1


def test_season_billable_games_includes_cancelled_unrefunded():
    season = _season(
        (
            Game(id=1, date=date(2026, 8, 4)),
            Game(id=2, date=date(2026, 8, 11), status=GameStatus.CANCELLED_UNREFUNDED),
        )
    )

    assert season.billable_games == 2
