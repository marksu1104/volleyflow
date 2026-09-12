"""Round-trip tests for the ORM mappings in db/models.py.

Most run against in-memory SQLite (tests/conftest.py). One test at the
bottom is marked `postgres` and hits the real Neon database instead, to
prove the schema actually works on Postgres, not just SQLite.
"""

from datetime import date, datetime
from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from volleyflow.db.engine import get_session
from volleyflow.db.models import ClubRow, GameRow, PlayerRow, SeasonRow
from volleyflow.schedule import GameStatus


def test_the_test_database_refuses_a_row_pointing_at_nothing(
    db_session: Session,
) -> None:
    """SQLite parses REFERENCES and then ignores it unless every
    connection says PRAGMA foreign_keys=ON (tests/conftest.py). Without
    that, four hundred tests run against a database that enforces less
    than the real one — which is how a route that wrote a child row
    before its parent passed CI and 500'd on Neon in front of a real
    organizer. This test fails the moment that PRAGMA goes missing.
    """
    db_session.add(GameRow(id=1, season_id=999, date=date(2026, 8, 4)))

    with pytest.raises(IntegrityError):
        db_session.commit()


def test_player_round_trips_through_sqlite(db_session: Session) -> None:
    db_session.add(PlayerRow(id=1, name="Alice"))
    db_session.commit()

    result = db_session.get(PlayerRow, 1)

    assert result is not None
    assert result.name == "Alice"


def test_season_stores_total_venue_cost_as_a_whole_number(
    db_session: Session,
) -> None:
    db_session.add(ClubRow(id=1, name="Test Club", created_at=datetime.now()))
    db_session.add(SeasonRow(id=1, club_id=1, total_venue_cost=Decimal("10000")))
    db_session.commit()

    result = db_session.get(SeasonRow, 1)

    assert result is not None
    assert result.total_venue_cost == Decimal("10000")


def test_game_stores_its_status(db_session: Session) -> None:
    db_session.add(ClubRow(id=1, name="Test Club", created_at=datetime.now()))
    db_session.add(SeasonRow(id=1, club_id=1, total_venue_cost=Decimal("10000")))
    # The season has to reach the database before the game that points at
    # it. models.py declares no relationship(), so the ORM has no idea
    # these two tables are related and flushes its mappers in alphabetical
    # order — GameRow before SeasonRow. Every route that writes a parent
    # and a child together already flushes between them for this reason;
    # this is the same rule, in a test.
    db_session.flush()
    db_session.add(
        GameRow(
            id=1,
            season_id=1,
            date=date(2026, 8, 4),
            status=GameStatus.CANCELLED_UNREFUNDED,
        )
    )
    db_session.commit()

    result = db_session.get(GameRow, 1)

    assert result is not None
    assert result.status == GameStatus.CANCELLED_UNREFUNDED


@pytest.mark.postgres
def test_player_round_trips_through_neon() -> None:
    with get_session() as session:
        session.add(PlayerRow(id=999_999, name="Postgres smoke test"))
        session.commit()

        result = session.get(PlayerRow, 999_999)
        assert result is not None
        assert result.name == "Postgres smoke test"

        session.delete(result)
        session.commit()
