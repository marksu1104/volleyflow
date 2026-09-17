"""Concurrent signups must never confirm more drop-ins than a game's
capacity allows.

Every other API test shares one SQLite session (see tests/api/conftest.py)
— fine for correctness, useless for concurrency, since there's nothing to
race against. This test runs against the real Neon database with no
dependency override, so each request gets its own session from the
connection pool exactly like production, and can genuinely race another
request for the same open slot. It's what actually exercises the
SELECT ... FOR UPDATE lock in routes/_attendance.py's _get_game_or_404 — SQLite ignores
that clause entirely, so this is the only place that would catch it
being accidentally removed.
"""

from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from volleyflow.api import auth
from volleyflow.api.main import app
from volleyflow.db.engine import get_session

pytestmark = pytest.mark.postgres

_TEST_PLAYER_PREFIX = "ConcurrencyTest-"


_PLAYER_CHILDREN = (
    "DELETE FROM waitlist_entries"
    " WHERE player_id = ANY(:ids) OR brought_by_player_id = ANY(:ids)",
    "DELETE FROM drop_ins"
    " WHERE player_id = ANY(:ids) OR brought_by_player_id = ANY(:ids)",
    "DELETE FROM absences WHERE player_id = ANY(:ids)",
    "DELETE FROM ledger_entries WHERE player_id = ANY(:ids)",
    "DELETE FROM season_members WHERE player_id = ANY(:ids)",
    "DELETE FROM club_members WHERE player_id = ANY(:ids)",
)
"""Every table that references players.id, children before parents.

Taken from db/models.py rather than remembered: drop_ins and
waitlist_entries each reference a player twice, once as the person and
once as whoever brought them, and missing the second one leaves exactly
the orphan this cleanup exists to prevent.
"""


def _cleanup(club_id: int, season_id: int) -> None:
    """Delete this run's rows — and anything a run that died left behind.

    The last statement used to be a global `DELETE FROM players WHERE
    name LIKE 'ConcurrencyTest-%'` while every delete above it was scoped
    to *this* club and season. That asymmetry holds only as long as no
    run ever fails. One did, on 2026-09-17, when the dev branch was
    missing three columns the models had started selecting: it left
    waitlist_entries and club_members behind for players that the next
    run then tried to delete, and Postgres refused. Every run after that
    failed at the same statement, on residue it had not created and
    could not reach — one red run turning into permanently red CI.

    So the sweep is symmetric now: find the test players, delete
    everything referencing them wherever it lives, then delete them.
    That also clears whatever an earlier failure stranded, which is the
    only way this database ever gets tidied — nobody holds credentials
    for it outside CI.
    """
    with get_session() as db:
        game_ids = [
            row[0]
            for row in db.execute(
                text("SELECT id FROM games WHERE season_id = :sid"),
                {"sid": season_id},
            ).all()
        ]
        if game_ids:
            for table in ("waitlist_entries", "drop_ins", "absences"):
                db.execute(
                    text(f"DELETE FROM {table} WHERE game_id = ANY(:ids)"),
                    {"ids": game_ids},
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

        # Scoped by player rather than by this run's club and season, so
        # rows stranded under a club and season this call knows nothing
        # about go too.
        player_ids = [
            row[0]
            for row in db.execute(
                text("SELECT id FROM players WHERE name LIKE :prefix"),
                {"prefix": f"{_TEST_PLAYER_PREFIX}%"},
            ).all()
        ]
        if player_ids:
            for statement in _PLAYER_CHILDREN:
                db.execute(text(statement), {"ids": player_ids})
            db.execute(
                text("DELETE FROM players WHERE id = ANY(:ids)"), {"ids": player_ids}
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
    monkeypatch.setattr(auth, "verify_id_token", lambda token: token)

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
    club = client.post("/clubs", json={"name": "concurrency 1"}).json()
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
        # routes/_people.py's _require_self_or_organizer), so one authorized caller
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


def test_adding_the_same_member_twice_at_once_is_refused_not_a_crash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The 500 reported three times on POST /seasons/{id}/members.

    Not a bug in the roster rules at all: `add_member` reads "are they
    already a member", gets no, and inserts — and two requests that
    overlap both read no. The primary key catches the loser, which used
    to surface as an unhandled IntegrityError.

    The roster screen produces exactly this overlap by itself: adding
    somebody reloads the whole roster, the reload redraws the buttons,
    and a second tap lands on the redrawn button while the first request
    is still in the air. tests/visual/smoke.js pressing every button on
    that page reproduced it; this is the same thing without a browser.

    Postgres-only, like the test above: the SQLite session every other
    API test shares has nothing to race against.
    """
    monkeypatch.setattr(auth, "verify_id_token", lambda token: token)

    client = TestClient(app)
    organizer_token = f"{_TEST_PLAYER_PREFIX}organizer"
    client.post(
        "/players/identify",
        json={
            "id_token": organizer_token,
            "display_name": f"{_TEST_PLAYER_PREFIX}Organizer",
        },
    )
    client.headers.update({"Authorization": f"Bearer {organizer_token}"})
    club_id = client.post("/clubs", json={"name": "concurrency 2"}).json()["id"]
    season_id = client.post(
        f"/clubs/{club_id}/seasons",
        json={
            "total_venue_cost": "1000",
            "game_dates": ["2031-01-14"],
            "member_names": [f"{_TEST_PLAYER_PREFIX}Member"],
            "capacity": 6,
        },
    ).json()["id"]

    try:
        newcomer = f"{_TEST_PLAYER_PREFIX}Newcomer"

        def add(_i: int) -> int:
            return client.post(
                f"/seasons/{season_id}/members", json={"player_name": newcomer}
            ).status_code

        with ThreadPoolExecutor(max_workers=4) as pool:
            codes = list(pool.map(add, range(4)))

        assert max(codes) < 500, f"a race must never be a crash: {codes}"
        assert codes.count(200) == 1, f"exactly one may win: {codes}"
        roster = client.get(f"/seasons/{season_id}").json()["members"]
        assert [m["name"] for m in roster].count(newcomer) == 1
    finally:
        _cleanup(club_id, season_id)


def test_two_removals_at_once_promote_two_different_people(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The 500 tests/visual/smoke.js hit on 2026-09-16, without a browser.

    Every path that frees a slot offers it to the queue, and the offer
    reads "who is first in line" and writes a drop-in for them. Two of
    those overlapping both read the same first name, and both write it:
    one person, two slots at one game, which
    uq_drop_ins_active_player_game refuses outright.

    Every other path that frees a slot goes through `_get_game_or_404`,
    which locks the game row, so two of those wait for each other.
    Taking somebody off the roster is the one that doesn't — and the
    roster screen produces the overlap by itself, the same way adding
    somebody does: the tap reloads the list, the reload redraws the
    buttons, and a second tap lands while the first request is still in
    the air. Two people coming off at once frees two places, and the
    queue is supposed to move up by two, not to hand the same place out
    twice.

    Postgres-only, like the rest of this file: the SQLite session the
    other API tests share has nothing to race against.
    """
    monkeypatch.setattr(auth, "verify_id_token", lambda token: token)

    client = TestClient(app)
    organizer_token = f"{_TEST_PLAYER_PREFIX}organizer"
    client.post(
        "/players/identify",
        json={
            "id_token": organizer_token,
            "display_name": f"{_TEST_PLAYER_PREFIX}Organizer",
        },
    )
    client.headers.update({"Authorization": f"Bearer {organizer_token}"})
    club_id = client.post("/clubs", json={"name": "concurrency 3"}).json()["id"]
    members = [f"{_TEST_PLAYER_PREFIX}M{i}" for i in range(3)]
    created = client.post(
        f"/clubs/{club_id}/seasons",
        json={
            "total_venue_cost": "900",
            "game_dates": ["2031-01-21"],
            "member_names": members,
            "capacity": 3,
        },
    ).json()
    season_id = created["id"]
    game_id = created["games"][0]["id"]

    try:
        # The court is full of fixed members, so both of these queue.
        waiting = [f"{_TEST_PLAYER_PREFIX}W{i}" for i in range(2)]
        for name in waiting:
            queued = client.post(
                "/drop-ins", json={"player_name": name, "game_id": game_id}
            )
            assert queued.json()["status"] == "waitlisted", queued.text

        roster = {
            m["name"]: m["id"]
            for m in client.get(f"/seasons/{season_id}").json()["members"]
        }

        def leave_roster(name: str) -> object:
            return client.delete(f"/seasons/{season_id}/members/{roster[name]}")

        with ThreadPoolExecutor(max_workers=2) as pool:
            answers = list(pool.map(leave_roster, members[:2]))

        assert [a.status_code for a in answers] == [204, 204], [a.text for a in answers]

        playing = client.get(f"/seasons/{season_id}").json()["games"][0][
            "confirmed_drop_ins"
        ]
        names = sorted(p["player_name"] for p in playing)
        assert names == sorted(waiting), (
            "two slots opened, so the queue moves up by two"
        )
    finally:
        _cleanup(club_id, season_id)
