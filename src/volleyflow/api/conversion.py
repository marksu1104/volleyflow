"""Bridges SQLAlchemy rows to the pure domain objects settlement.py expects.

The domain layer never imports SQLAlchemy (see the import-linter contract
in pyproject.toml) — this module is where that boundary gets crossed, in
the one direction it's allowed to go: db row -> domain object.
"""

from volleyflow.attendance import Absence, DropIn
from volleyflow.db.models import (
    AbsenceRow,
    DropInRow,
    GameRow,
    LedgerEntryRow,
    PlayerRow,
    SeasonRow,
)
from volleyflow.ledger import LedgerEntry
from volleyflow.players import Player
from volleyflow.schedule import Game, Season


def player_from_row(row: PlayerRow) -> Player:
    return Player(id=row.id, name=row.name)


def game_from_row(row: GameRow) -> Game:
    return Game(id=row.id, date=row.date, status=row.status)


def season_from_rows(
    season_row: SeasonRow, game_rows: list[GameRow], member_rows: list[PlayerRow]
) -> Season:
    return Season(
        id=season_row.id,
        total_venue_cost=season_row.total_venue_cost,
        games=tuple(game_from_row(g) for g in game_rows),
        members=tuple(player_from_row(p) for p in member_rows),
    )


def absence_from_row(
    row: AbsenceRow, players_by_id: dict[int, Player], games_by_id: dict[int, Game]
) -> Absence:
    return Absence(
        player=players_by_id[row.player_id],
        game=games_by_id[row.game_id],
        recorded_at=row.recorded_at,
    )


def drop_in_from_row(
    row: DropInRow, players_by_id: dict[int, Player], games_by_id: dict[int, Game]
) -> DropIn:
    return DropIn(
        player=players_by_id[row.player_id],
        game=games_by_id[row.game_id],
        signed_up_at=row.signed_up_at,
        cancelled_at=row.cancelled_at,
    )


def ledger_entry_from_row(row: LedgerEntryRow, player: Player) -> LedgerEntry:
    return LedgerEntry(
        player=player,
        entry_type=row.entry_type,
        amount=row.amount,
        recorded_at=row.recorded_at,
    )
