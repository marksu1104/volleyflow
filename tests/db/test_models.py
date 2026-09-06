"""Round-trip tests for the ORM mappings in db/models.py.

Most run against in-memory SQLite (tests/conftest.py). One test at the
bottom is marked `postgres` and hits the real Neon database instead, to
prove the schema actually works on Postgres, not just SQLite.
"""

from datetime import date, datetime
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from volleyflow.db.engine import get_session
from volleyflow.db.models import ClubRow, GameRow, PlayerRow, SeasonRow
from volleyflow.schedule import GameStatus


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
