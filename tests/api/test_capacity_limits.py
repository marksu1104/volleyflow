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


def test_a_name_given_twice_becomes_one_member(client: TestClient) -> None:
    # A roster is a set. The same person named twice used to build two
    # identical season_members rows and fail on the primary key — a 500
    # for something that isn't even an error.
    club = create_club(client)

    response = client.post(
        f"/clubs/{club['id']}/seasons",
        json={
            "total_venue_cost": "10000",
            "game_dates": ["2026-08-18"],
            "member_names": ["Alice", "Bob", "Alice"],
            "capacity": 5,
        },
    )

    assert response.status_code == 200
    names = [
        m["name"]
        for m in client.get(f"/seasons/{response.json()['id']}").json()["members"]
    ]
    assert sorted(names) == ["Alice", "Bob"]


def test_adding_someone_already_on_the_roster_is_refused_not_a_crash(
    client: TestClient,
) -> None:
    season = start_season(client, member_names=["Alice"], capacity=5)

    response = client.post(
        f"/seasons/{season['id']}/members", json={"player_name": "Alice"}
    )

    assert response.status_code == 400
    assert "already a member" in response.json()["detail"].lower()


def test_removing_a_member_a_drop_in_was_covering_can_be_undone(
    client: TestClient,
) -> None:
    """Reported from real use, 2026-09-12: 18 members, remove one, the
    roster reads 17 — and adding them back says to raise the capacity to
    19.

    Removing them closes their leave but leaves their substitute on
    court, so the game stays at capacity while the roster is one short.
    The person adds no body to that game on the way back in, because
    rejoining restores the leave the removal closed, so it must not be
    refused for having no room.
    """
    season = start_season(client, member_names=["Alice", "Bob", "Carol"], capacity=3)
    game_id = season["games"][0]["id"]
    alice = client.get(f"/seasons/{season['id']}").json()["members"][0]
    client.post("/absences", json={"player_name": "Alice", "game_id": game_id})
    client.post("/drop-ins", json={"player_name": "訪客", "game_id": game_id})

    client.delete(f"/seasons/{season['id']}/members/{alice['id']}")
    back = client.post(
        f"/seasons/{season['id']}/members", json={"player_name": "Alice"}
    )

    assert back.status_code == 200, back.json()
    game = client.get(f"/seasons/{season['id']}").json()["games"][0]
    assert [a["player_name"] for a in game["absences"]] == ["Alice"], (
        "the leave the removal closed comes back with her"
    )
    assert [d["player_name"] for d in game["confirmed_drop_ins"]] == ["訪客"], (
        "and her stand-in keeps the slot, having released nothing"
    )


def test_undoing_a_removal_does_not_overfill_the_court(client: TestClient) -> None:
    # The other half of the rule above: the exemption must not become a
    # way past capacity. Three members, one away, one drop-in covering —
    # the court is full, and it is still full after the round trip.
    season = start_season(client, member_names=["Alice", "Bob", "Carol"], capacity=3)
    game_id = season["games"][0]["id"]
    alice = client.get(f"/seasons/{season['id']}").json()["members"][0]
    client.post("/absences", json={"player_name": "Alice", "game_id": game_id})
    client.post("/drop-ins", json={"player_name": "訪客", "game_id": game_id})
    client.delete(f"/seasons/{season['id']}/members/{alice['id']}")
    client.post(f"/seasons/{season['id']}/members", json={"player_name": "Alice"})

    extra = client.post("/drop-ins", json={"player_name": "另一位", "game_id": game_id})

    assert extra.json()["status"] == "waitlisted"


def test_a_member_who_cancelled_their_own_leave_does_not_get_it_back(
    client: TestClient,
) -> None:
    # Only leave a *removal* closed comes back. Somebody who said they
    # were coming after all, was taken off the roster and later added
    # back is simply expected, exactly as they were.
    season = start_season(client, member_names=["Alice", "Bob"], capacity=4)
    game_id = season["games"][0]["id"]
    alice = client.get(f"/seasons/{season['id']}").json()["members"][0]
    absence = client.post(
        "/absences", json={"player_name": "Alice", "game_id": game_id}
    ).json()
    client.post(f"/absences/{absence['id']}/cancel", json={})

    client.delete(f"/seasons/{season['id']}/members/{alice['id']}")
    client.post(f"/seasons/{season['id']}/members", json={"player_name": "Alice"})

    game = client.get(f"/seasons/{season['id']}").json()["games"][0]
    assert game["absences"] == []


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
