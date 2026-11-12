"""The one-off correction of drop-in charges that predate keeping them
in step with the night's price."""

from decimal import Decimal
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

import scripts.correct_drop_in_charges as script
from tests.api.factories import start_season
from volleyflow.api.routes._people import _now
from volleyflow.db.models import SeasonRow


def _owed(client: TestClient, season: dict[str, Any], player_id: int) -> Decimal:
    ledger = client.get(f"/clubs/{season['club_id']}/players/{player_id}/ledger").json()
    return -Decimal(ledger["balance"])


def _stale_drop_in(
    client: TestClient, db_session: Session
) -> tuple[dict[str, Any], int]:
    """Dan charged at signup, then the venue cost cut behind the API's
    back, the way a price change used to leave a drop-in."""
    season = start_season(
        client, total_venue_cost="20000", member_names=["Alice"], capacity=4
    )
    dan = client.post(
        "/drop-ins",
        json={
            "player_name": "Dan",
            "game_id": season["games"][0]["id"],
            "gender": "male",
        },
    ).json()
    row = db_session.get(SeasonRow, season["id"])
    assert row is not None
    row.total_venue_cost = Decimal("16000")
    db_session.commit()
    return season, dan["player_id"]


def test_a_stale_charge_is_listed_without_being_changed(
    client: TestClient, db_session: Session
) -> None:
    season, dan = _stale_drop_in(client, db_session)

    stale = script.find(db_session)

    assert [(s.player_id, s.charged, s.price) for s in stale] == [
        (dan, Decimal("2500"), Decimal("2000"))
    ]
    assert _owed(client, season, dan) == Decimal("2500"), "finding writes nothing"


def test_correcting_brings_the_charge_to_the_nights_price_once(
    client: TestClient, db_session: Session
) -> None:
    season, dan = _stale_drop_in(client, db_session)

    first = script.correct(db_session)
    db_session.commit()
    second = script.correct(db_session)

    assert (first, second) == (1, 0)
    assert _owed(client, season, dan) == Decimal("2000")
    assert script.find(db_session) == []


def test_a_settled_season_is_listed_but_never_corrected(
    client: TestClient, db_session: Session
) -> None:
    season, dan = _stale_drop_in(client, db_session)
    row = db_session.get(SeasonRow, season["id"])
    assert row is not None
    row.settled_at = _now()
    db_session.commit()

    corrected = script.correct(db_session)

    assert corrected == 0
    assert [s.settled for s in script.find(db_session)] == [True]
    assert _owed(client, season, dan) == Decimal("2500")
