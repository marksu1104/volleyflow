"""Shared test setup helpers for API tests."""

from typing import Any

from fastapi.testclient import TestClient


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
) -> dict[str, Any]:
    response = client.post(
        "/seasons",
        json={
            "total_venue_cost": total_venue_cost,
            "game_dates": game_dates or ["2026-08-18", "2026-08-25"],
            "member_names": member_names or ["Alice", "Bob"],
            "capacity": capacity,
            "minimum_roster": minimum_roster,
            "game_start_time": game_start_time,
            "game_end_time": game_end_time,
            "location": location,
        },
    )
    assert response.status_code == 200
    return response.json()  # type: ignore[no-any-return]
