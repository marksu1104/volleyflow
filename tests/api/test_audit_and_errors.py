"""The developer overview's audit trail, the server's errors, and the
figures that say whether anything is in use.

The trail is written in the same transaction as each change, so what is
tested is that the change and its record arrive together, with the names
and amounts as they were — and that only the developer can read either.
"""

from collections.abc import Iterator
from datetime import datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.api.factories import auth_headers, create_club, identify, start_season
from volleyflow.api.dependencies import get_db
from volleyflow.api.error_reporting import ErrorReportingMiddleware, store_error_event
from volleyflow.api.routes._people import _today_in_taiwan
from volleyflow.db.models import ErrorEventRow


def _developer(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    dev = identify(client, "Developer")
    monkeypatch.setenv("DEVELOPER_LINE_USER_ID", dev["token"])
    return dev


def _activity(client: TestClient, dev: dict[str, Any]) -> list[dict[str, Any]]:
    response = client.get("/developer/activity", headers=auth_headers(dev["token"]))
    assert response.status_code == 200, response.text
    body: list[dict[str, Any]] = response.json()
    return body


def _member_id(client: TestClient, season: dict[str, Any], name: str) -> int:
    members = client.get(f"/seasons/{season['id']}").json()["members"]
    return int(next(m["id"] for m in members if m["name"] == name))


def test_a_payment_is_on_the_record_with_who_and_how_much(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    season = start_season(client, member_names=["Alice", "Bob"])
    alice = _member_id(client, season, "Alice")

    client.post(
        f"/clubs/{season['club_id']}/players/{alice}/payments", json={"amount": "2875"}
    )

    dev = _developer(client, monkeypatch)
    latest = _activity(client, dev)[0]
    assert latest["action"] == "payment"
    assert latest["subject_name"] == "Alice"
    assert latest["amount"] == "2875"
    assert latest["club_name"] == "Test Club"
    assert latest["actor_name"]


def test_creating_a_club_is_on_the_record(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    create_club(client, name="Night Owls")

    dev = _developer(client, monkeypatch)
    latest = _activity(client, dev)[0]
    assert (latest["action"], latest["club_name"]) == ("club_created", "Night Owls")


def test_undoing_a_payment_is_its_own_record(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    season = start_season(client, member_names=["Alice"])
    alice = _member_id(client, season, "Alice")
    paid = client.post(
        f"/clubs/{season['club_id']}/players/{alice}/payments", json={"amount": "500"}
    ).json()

    client.post(f"/ledger-entries/{paid['id']}/reverse")

    dev = _developer(client, monkeypatch)
    actions = [e["action"] for e in _activity(client, dev)]
    assert actions[:2] == ["payment_undone", "payment"], "newest first"


def test_removing_somebody_from_the_club_is_on_the_record(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    season = start_season(client, member_names=["Alice"])
    guest = client.post(
        f"/clubs/{season['club_id']}/guests", json={"name": "Dave"}
    ).json()

    response = client.delete(f"/clubs/{season['club_id']}/members/{guest['id']}")

    assert response.status_code == 204, response.text
    dev = _developer(client, monkeypatch)
    latest = _activity(client, dev)[0]
    assert (latest["action"], latest["subject_name"]) == ("member_removed", "Dave")


def test_a_refused_change_leaves_no_record(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Written in the same transaction: a change that does not happen
    # must not appear to have happened.
    season = start_season(client, member_names=["Alice"])
    alice = _member_id(client, season, "Alice")

    refused = client.delete(f"/clubs/{season['club_id']}/members/{alice}")

    assert refused.status_code == 400
    dev = _developer(client, monkeypatch)
    assert "member_removed" not in [e["action"] for e in _activity(client, dev)]


def test_a_new_venue_cost_records_the_old_one(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    season = start_season(client, total_venue_cost="10000", member_names=["Alice"])

    client.patch(f"/seasons/{season['id']}", json={"total_venue_cost": "12000"})

    dev = _developer(client, monkeypatch)
    latest = _activity(client, dev)[0]
    assert latest["action"] == "venue_cost_changed"
    assert latest["amount"] == "12000"
    assert latest["detail"] == "10000"


def test_a_change_that_moves_no_money_is_not_recorded(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    season = start_season(client, member_names=["Alice"])
    dev = _developer(client, monkeypatch)
    before = len(_activity(client, dev))

    client.patch(f"/seasons/{season['id']}", json={"location": "晴光館"})

    assert len(_activity(client, dev)) == before


def test_only_the_figure_that_changed_is_recorded(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The settings sheets send every figure they hold: 費用 both the venue
    # cost and the AC fee, 場地與規則 the capacity with the times. Only
    # what actually changed belongs on the record.
    season = start_season(client, total_venue_cost="10000", member_names=["Alice"])
    capacity = client.get(f"/seasons/{season['id']}").json()["capacity"]

    client.patch(
        f"/seasons/{season['id']}",
        json={"total_venue_cost": "12000", "ac_surcharge": "0"},
    )
    client.patch(
        f"/seasons/{season['id']}", json={"capacity": capacity, "location": "晴光館"}
    )

    dev = _developer(client, monkeypatch)
    actions = [e["action"] for e in _activity(client, dev)]
    assert actions.count("venue_cost_changed") == 1
    assert "ac_surcharge_changed" not in actions
    assert "capacity_changed" not in actions


def test_cancelling_a_game_and_the_night_air_conditioning_are_recorded(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    season = start_season(
        client, game_dates=["2031-05-06", "2031-05-13"], member_names=["Alice"]
    )
    first, second = (g["id"] for g in season["games"])

    client.post(f"/games/{first}/cancel", json={"refunded": True})
    client.put(f"/games/{second}/air-conditioning", json={"air_conditioned": True})

    dev = _developer(client, monkeypatch)
    trail = _activity(client, dev)
    assert (trail[0]["action"], trail[0]["detail"]) == ("ac_on", "2031-05-13")
    assert (trail[1]["action"], trail[1]["detail"]) == (
        "game_cancelled_refunded",
        "2031-05-06",
    )


def test_settling_a_season_is_recorded_with_its_dates(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    season = start_season(
        client, game_dates=["2020-01-07", "2020-01-14"], member_names=["Alice"]
    )

    response = client.post(f"/seasons/{season['id']}/settle", json={})

    assert response.status_code == 200, response.text
    dev = _developer(client, monkeypatch)
    latest = _activity(client, dev)[0]
    assert (latest["action"], latest["detail"]) == (
        "season_settled",
        "2020-01-07~2020-01-14",
    )


def test_only_the_developer_reads_the_trail_or_the_errors(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    start_season(client, member_names=["Alice"])
    _developer(client, monkeypatch)

    # The club's own organizer is not the developer.
    assert client.get("/developer/activity").status_code == 403
    assert client.get("/developer/errors").status_code == 403


def _crashing_app(db_session: Session) -> FastAPI:
    """The real middleware and store around a route that always throws,
    writing to the test database the way main.app writes to the real one."""
    app = FastAPI()
    app.add_middleware(ErrorReportingMiddleware)
    app.state.error_store = store_error_event

    def override() -> Iterator[Session]:
        yield db_session

    app.dependency_overrides[get_db] = override

    @app.post("/absences")
    def boom() -> None:
        raise RuntimeError("kaboom")

    return app


def test_a_crash_is_kept_with_where_it_came_from(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    crashing = TestClient(_crashing_app(db_session), raise_server_exceptions=False)

    response = crashing.post(
        "/absences",
        headers={"Referer": "https://x.example/volleyflow/member.html?as=1"},
    )

    assert response.status_code == 500
    dev = _developer(client, monkeypatch)
    errors = client.get("/developer/errors", headers=auth_headers(dev["token"])).json()
    assert len(errors) == 1
    kept = errors[0]
    assert (kept["method"], kept["path"], kept["page"]) == (
        "POST",
        "/absences",
        "member.html",
    )
    assert kept["error_type"] == "RuntimeError"
    assert kept["message"] == "kaboom"
    assert "test_audit_and_errors.py" not in kept["location"], (
        "only this project's frames"
    )


def test_old_errors_are_dropped_and_last_weeks_are_listed(
    client: TestClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = datetime.now()
    db_session.add_all(
        [
            ErrorEventRow(
                occurred_at=now - timedelta(days=days),
                method="GET",
                path=f"/old-{days}",
                page=None,
                error_type="RuntimeError",
                message="x",
                location="?",
            )
            for days in (3, 10, 40)
        ]
    )
    db_session.commit()
    crashing = TestClient(_crashing_app(db_session), raise_server_exceptions=False)

    crashing.post("/absences")

    dev = _developer(client, monkeypatch)
    paths = [
        e["path"]
        for e in client.get(
            "/developer/errors", headers=auth_headers(dev["token"])
        ).json()
    ]
    assert paths == ["/absences", "/old-3"], "a week, newest first"
    kept = {row.path for row in db_session.query(ErrorEventRow)}
    assert "/old-40" not in kept, "past thirty days the row goes"
    assert "/old-10" in kept


def test_the_overview_counts_this_weeks_games_and_signups(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    today = _today_in_taiwan().isoformat()
    season = start_season(client, game_dates=[today], member_names=["Alice"])
    client.post(
        "/drop-ins", json={"player_name": "Carol", "game_id": season["games"][0]["id"]}
    )

    dev = _developer(client, monkeypatch)
    body = client.get("/developer/overview", headers=auth_headers(dev["token"])).json()

    assert body["active_clubs"] >= 1
    assert body["games_this_week"] >= 1
    assert body["signups_this_week"] >= 1
