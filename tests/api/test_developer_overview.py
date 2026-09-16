"""The developer's read-only view of the whole installation.

Gated the same way problem reports are: named by an environment
variable, refused outright when that is unset. See routes/developer.py.
"""

from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.api.factories import auth_headers, identify, start_season


def _developer(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """The one person who may read this — named by an environment
    variable, not a role anybody could be granted through the app."""
    dev = identify(client, "Developer")
    monkeypatch.setenv("DEVELOPER_LINE_USER_ID", dev["token"])
    return dev


def test_the_overview_counts_what_is_actually_there(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    season = start_season(
        client, game_dates=["2031-05-06", "2031-05-13"], member_names=["Alice", "Bob"]
    )
    client.post(
        "/drop-ins", json={"player_name": "Carol", "game_id": season["games"][0]["id"]}
    )
    dev = _developer(client, monkeypatch)

    overview = client.get("/developer/overview", headers=auth_headers(dev["token"]))

    assert overview.status_code == 200, overview.text
    body = overview.json()
    assert body["clubs"] >= 1
    assert body["seasons"] >= 1
    assert body["games"] >= 2
    assert body["club_members"] >= 1
    assert body["drop_ins"] >= 1
    assert body["ledger_entries"] >= 1
    assert "Test Club" in body["newest_clubs"]


def test_a_cancelled_signup_is_history_not_a_person_expecting_to_play(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    season = start_season(client, game_dates=["2031-05-06"], member_names=["Alice"])
    signed_up = client.post(
        "/drop-ins", json={"player_name": "Carol", "game_id": season["games"][0]["id"]}
    ).json()
    dev = _developer(client, monkeypatch)
    before = client.get(
        "/developer/overview", headers=auth_headers(dev["token"])
    ).json()

    client.post(f"/drop-ins/{signed_up['id']}/cancel")

    after = client.get("/developer/overview", headers=auth_headers(dev["token"])).json()
    assert after["drop_ins"] == before["drop_ins"] - 1


def test_somebody_waiting_to_be_approved_is_counted(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The one figure that means a person is stuck behind an organizer who
    # hasn't looked.
    from volleyflow.api.invites import invite_token

    season = start_season(client, member_names=["Alice"])
    dev = _developer(client, monkeypatch)
    before = client.get(
        "/developer/overview", headers=auth_headers(dev["token"])
    ).json()

    newcomer = identify(client, "Newcomer")
    client.post(
        f"/clubs/{season['club_id']}/join",
        json={"invite": invite_token(season["club_id"])},
        headers=auth_headers(newcomer["token"]),
    )

    after = client.get("/developer/overview", headers=auth_headers(dev["token"])).json()
    assert after["pending_members"] == before["pending_members"] + 1


def test_an_ordinary_player_may_not_read_it(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _developer(client, monkeypatch)
    nosy = identify(client, "Nosy")

    refused = client.get("/developer/overview", headers=auth_headers(nosy["token"]))

    assert refused.status_code == 403


def test_a_server_without_a_developer_refuses_rather_than_guessing(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Unset means nobody, never everybody — the same rule the invite
    # secret and the report reader follow.
    monkeypatch.delenv("DEVELOPER_LINE_USER_ID", raising=False)
    somebody = identify(client, "Somebody")

    refused = client.get("/developer/overview", headers=auth_headers(somebody["token"]))

    assert refused.status_code == 503
