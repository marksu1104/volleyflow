"""Shared test setup helpers for API tests."""

import uuid
from typing import Any

from fastapi.testclient import TestClient


def create_club(client: TestClient, name: str = "Test Club") -> dict[str, Any]:
    """A fresh club with a fresh organizer — line_user_id is randomized
    so tests that create several clubs never collide on it.
    """
    organizer = client.post(
        "/players/identify",
        json={
            "line_user_id": f"test-{uuid.uuid4()}",
            "display_name": "Test Organizer",
        },
    ).json()
    response = client.post("/clubs", json={"name": name, "player_id": organizer["id"]})
    assert response.status_code == 200
    body: dict[str, Any] = response.json()
    body["organizer_id"] = organizer["id"]
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
) -> dict[str, Any]:
    if club_id is None:
        club_id = create_club(client)["id"]

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
    )
    assert response.status_code == 200
    body: dict[str, Any] = response.json()
    body["club_id"] = club_id
    return body
