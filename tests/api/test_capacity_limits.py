"""Capacity is a hard limit everywhere a roster can grow — at season
creation, adding a member mid-season (test_season_fee_ledger.py covers
that path), and lowering capacity itself. See CLAUDE.md 2.3: "Nothing may
put more people on court than Season.capacity, including the organizer."
"""

from fastapi.testclient import TestClient

from tests.api.factories import create_club, start_season


def test_a_season_cannot_start_with_more_members_than_capacity(
    client: TestClient,
) -> None:
    club = create_club(client)

    response = client.post(
        f"/clubs/{club['id']}/seasons",
        json={
            "total_venue_cost": "10000",
            "game_dates": ["2026-08-18"],
            "member_names": ["Alice", "Bob", "Carol"],
            "capacity": 2,
        },
    )

    assert response.status_code == 400
    assert "capacity" in response.json()["detail"].lower()


def test_a_season_with_exactly_capacity_members_is_fine(client: TestClient) -> None:
    club = create_club(client)

    response = client.post(
        f"/clubs/{club['id']}/seasons",
        json={
            "total_venue_cost": "10000",
            "game_dates": ["2026-08-18"],
            "member_names": ["Alice", "Bob"],
            "capacity": 2,
        },
    )

    assert response.status_code == 200


def test_capacity_cannot_drop_below_the_current_roster(client: TestClient) -> None:
    season = start_season(client, member_names=["Alice", "Bob", "Carol"], capacity=3)

    response = client.patch(f"/seasons/{season['id']}", json={"capacity": 2})

    assert response.status_code == 400
    assert "capacity" in response.json()["detail"].lower()


def test_capacity_cannot_drop_below_a_games_confirmed_drop_ins(
    client: TestClient,
) -> None:
    season = start_season(client, member_names=["Alice"], capacity=3)
    game_id = season["games"][0]["id"]
    client.post("/drop-ins", json={"player_name": "甲", "game_id": game_id})
    client.post("/drop-ins", json={"player_name": "乙", "game_id": game_id})
    # 3 on court: Alice plus two drop-ins. Dropping to 2 would leave one
    # of them with nowhere to have been.

    response = client.patch(f"/seasons/{season['id']}", json={"capacity": 2})

    assert response.status_code == 400


def test_capacity_can_drop_to_exactly_what_is_already_there(
    client: TestClient,
) -> None:
    season = start_season(client, member_names=["Alice", "Bob"], capacity=5)

    response = client.patch(f"/seasons/{season['id']}", json={"capacity": 2})

    assert response.status_code == 200
    assert response.json()["capacity"] == 2


def test_capacity_can_always_be_raised(client: TestClient) -> None:
    season = start_season(client, member_names=["Alice", "Bob"], capacity=2)

    response = client.patch(f"/seasons/{season['id']}", json={"capacity": 10})

    assert response.status_code == 200
    assert response.json()["capacity"] == 10
