"""Concurrent signups must never confirm more drop-ins than a game's
capacity allows.

Every other API test shares one SQLite session (see tests/api/conftest.py)
— fine for correctness, useless for concurrency, since there's nothing to
race against. This test runs against the real Neon database with no
dependency override, so each request gets its own session from the
connection pool exactly like production, and can genuinely race another
request for the same open slot. It's what actually exercises the
SELECT ... FOR UPDATE lock in routes._get_game_or_404 — SQLite ignores
that clause entirely, so this is the only place that would catch it
being accidentally removed.
"""

from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from volleyflow.api import routes
from volleyflow.api.main import app
from volleyflow.db.engine import get_session

pytestmark = pytest.mark.postgres

_TEST_PLAYER_PREFIX = "ConcurrencyTest-"


def _cleanup(club_id: int, season_id: int) -> None:
    with get_session() as db:
        game_ids = [
            row[0]
            for row in db.execute(
                text("SELECT id FROM games WHERE season_id = :sid"),
                {"sid": season_id},
            ).all()
        ]
        db.execute(
            text("DELETE FROM waitlist_entries WHERE game_id = ANY(:ids)"),
            {"ids": game_ids},
        )
        db.execute(
            text("DELETE FROM drop_ins WHERE game_id = ANY(:ids)"), {"ids": game_ids}
        )
        db.execute(
            text("DELETE FROM absences WHERE game_id = ANY(:ids)"), {"ids": game_ids}
        )
        db.execute(
            text("DELETE FROM ledger_entries WHERE season_id = :sid"),
            {"sid": season_id},
        )
        db.execute(text("DELETE FROM games WHERE season_id = :sid"), {"sid": season_id})
        db.execute(
            text("DELETE FROM season_members WHERE season_id = :sid"),
            {"sid": season_id},
        )
        db.execute(text("DELETE FROM seasons WHERE id = :sid"), {"sid": season_id})
        db.execute(
            text("DELETE FROM club_members WHERE club_id = :cid"), {"cid": club_id}
        )
        db.execute(text("DELETE FROM clubs WHERE id = :cid"), {"cid": club_id})
        db.execute(
            text("DELETE FROM players WHERE name LIKE :prefix"),
            {"prefix": f"{_TEST_PLAYER_PREFIX}%"},
        )
        db.commit()


def test_concurrent_signups_never_exceed_capacity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Real ID token verification means a real call to LINE — not
    # available here. Same fake as tests/api/conftest.py's client
    # fixture, applied by hand since this test deliberately doesn't use
    # that fixture (it needs a dependency-override-free TestClient to
    # exercise real connection-pool concurrency).
    monkeypatch.setattr(routes, "verify_id_token", lambda token: token)

    client = TestClient(app)
    capacity = 4
    open_slots = capacity - 1  # the one fixed member already fills one slot
    contenders = open_slots + 3  # more racers than slots, on purpose

    organizer_token = f"{_TEST_PLAYER_PREFIX}organizer"
    client.post(
        "/players/identify",
        json={
            "id_token": organizer_token,
            "display_name": f"{_TEST_PLAYER_PREFIX}Organizer",
        },
    )
    client.headers.update({"Authorization": f"Bearer {organizer_token}"})
    club = client.post("/clubs", json={"name": f"{_TEST_PLAYER_PREFIX}Club"}).json()
    club_id = club["id"]

    create = client.post(
        f"/clubs/{club_id}/seasons",
        json={
            "total_venue_cost": "1000",
            "game_dates": ["2031-01-07"],
            "member_names": [f"{_TEST_PLAYER_PREFIX}Member"],
            "capacity": capacity,
        },
    )
    assert create.status_code == 200
    body = create.json()
    season_id = body["id"]
    game_id = body["games"][0]["id"]

    try:
        # Every racer signs up under the organizer's identity — the
        # concurrency being tested is about the game's capacity, not
        # about who's allowed to sign someone up (see
        # routes._require_self_or_organizer), so one authorized caller
        # racing itself N times exercises the same lock.
        def sign_up(i: int) -> str:
            res = client.post(
                "/drop-ins",
                json={
                    "player_name": f"{_TEST_PLAYER_PREFIX}P{i}",
                    "game_id": game_id,
                },
            )
            assert res.status_code == 200
            status: str = res.json()["status"]
            return status

        with ThreadPoolExecutor(max_workers=contenders) as pool:
            results = list(pool.map(sign_up, range(contenders)))

        assert results.count("confirmed") == open_slots
        assert results.count("waitlisted") == contenders - open_slots
    finally:
        _cleanup(club_id, season_id)
