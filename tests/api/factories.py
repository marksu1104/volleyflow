"""Shared test setup helpers for API tests."""

import uuid
from typing import Any

from fastapi.testclient import TestClient


def auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def create_club(client: TestClient, name: str = "Test Club") -> dict[str, Any]:
    """A fresh club with a fresh organizer — the token is a random string
    that the test verify_id_token fake (see conftest.client) treats as
    its own line_user_id, so tests that create several clubs never
    collide on identity.

    Sets `client.headers["Authorization"]` to the new organizer's token
    as a side effect: every request this `client` makes afterwards acts
    as that organizer unless a test overrides the header itself (e.g.
    to test a *different* identity, or to check what happens with none
    at all). This is why the overwhelming majority of existing tests —
    which care about domain behavior, not who's allowed to trigger it —
    needed no changes when auth landed: the organizer can always act on
    anyone in their own club (see routes._require_self_or_organizer),
    so acting as the organizer by default preserves their behavior
    exactly.
    """
    token = f"test-{uuid.uuid4()}"
    organizer = client.post(
        "/players/identify",
        json={"id_token": token, "display_name": "Test Organizer"},
    ).json()
    response = client.post("/clubs", json={"name": name}, headers=auth_headers(token))
    assert response.status_code == 200
    body: dict[str, Any] = response.json()
    body["organizer_id"] = organizer["id"]
    body["organizer_token"] = token
    client.headers.update(auth_headers(token))
    return body


def start_season(
    client: TestClient,
    total_venue_cost: str = "10000",
    game_dates: list[str] | None = None,
    member_names: list[str] | None = None,
    capacity: int = 18,
    minimum_roster: int = 12,
    game_start_time: str | None = None,
    game_end_time: str | None = None,
    location: str | None = None,
    change_deadline_days: int | None = None,
    club_id: int | None = None,
    organizer_token: str | None = None,
) -> dict[str, Any]:
    """organizer_token only needs passing when club_id names a club this
    `client` didn't just create (e.g. a second club in the same test) —
    otherwise create_club already left the right token on the client.
    """
    if club_id is None:
        club = create_club(client)
        club_id = club["id"]
        organizer_token = organizer_token or club["organizer_token"]

    headers = auth_headers(organizer_token) if organizer_token else None
    response = client.post(
        f"/clubs/{club_id}/seasons",
        json={
            "total_venue_cost": total_venue_cost,
            "game_dates": game_dates or ["2026-08-18", "2026-08-25"],
            "member_names": member_names or ["Alice", "Bob"],
            "capacity": capacity,
            "minimum_roster": minimum_roster,
            "game_start_time": game_start_time,
            "game_end_time": game_end_time,
            "location": location,
            "change_deadline_days": change_deadline_days,
        },
        headers=headers,
    )
    assert response.status_code == 200
    body: dict[str, Any] = response.json()
    body["club_id"] = club_id
    if organizer_token:
        body["organizer_token"] = organizer_token
    return body


def identify(
    client: TestClient, display_name: str, token: str | None = None
) -> dict[str, Any]:
    """Resolves a LINE identity without touching `client.headers` — for
    tests that need a *second* identified player (a non-organizer club
    member, a stranger) without disturbing the organizer token already
    on the client. Returns the resolved player plus its own token, so
    the caller can attach it explicitly per-request with auth_headers.
    """
    token = token or f"test-{uuid.uuid4()}"
    body: dict[str, Any] = client.post(
        "/players/identify",
        json={"id_token": token, "display_name": display_name},
    ).json()
    body["token"] = token
    return body
