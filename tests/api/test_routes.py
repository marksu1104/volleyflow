"""Tests for the API routes."""

from typing import Any

from fastapi.testclient import TestClient


def _start_season(
    client: TestClient,
    total_venue_cost: str = "10000",
    game_dates: list[str] | None = None,
    member_names: list[str] | None = None,
    capacity: int = 18,
) -> dict[str, Any]:
    response = client.post(
        "/seasons",
        json={
            "total_venue_cost": total_venue_cost,
            "game_dates": game_dates or ["2026-08-18", "2026-08-25"],
            "member_names": member_names or ["Alice", "Bob"],
            "capacity": capacity,
        },
    )
    assert response.status_code == 200
    return response.json()  # type: ignore[no-any-return]


def test_start_season_creates_games_and_members(client: TestClient) -> None:
    body = _start_season(client)

    assert body["total_venue_cost"] == "10000"
    assert len(body["games"]) == 2
    assert len(body["member_ids"]) == 2


def test_start_season_reuses_an_existing_player_by_name(client: TestClient) -> None:
    first = _start_season(client, member_names=["Alice"])
    second = _start_season(client, member_names=["Alice"])

    assert first["member_ids"] == second["member_ids"]


def test_start_season_rejects_an_empty_game_list(client: TestClient) -> None:
    response = client.post(
        "/seasons",
        json={
            "total_venue_cost": "10000",
            "game_dates": [],
            "member_names": ["Alice"],
        },
    )

    assert response.status_code == 422


def test_record_absence_for_a_member_succeeds(client: TestClient) -> None:
    season = _start_season(client)
    game_id = season["games"][0]["id"]

    response = client.post(
        "/absences", json={"player_name": "Alice", "game_id": game_id}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["player_id"] == season["member_ids"][0]
    assert body["game_id"] == game_id


def test_record_absence_for_an_unknown_game_returns_404(client: TestClient) -> None:
    _start_season(client)

    response = client.post(
        "/absences", json={"player_name": "Alice", "game_id": 999_999}
    )

    assert response.status_code == 404


def test_record_absence_for_an_unknown_player_returns_404(client: TestClient) -> None:
    season = _start_season(client)
    game_id = season["games"][0]["id"]

    response = client.post(
        "/absences", json={"player_name": "Nobody", "game_id": game_id}
    )

    assert response.status_code == 404


def test_record_absence_for_a_non_member_returns_400(client: TestClient) -> None:
    season = _start_season(client, member_names=["Alice"])
    game_id = season["games"][0]["id"]
    _start_season(client, member_names=["Carol"])  # Carol exists, wrong season

    response = client.post(
        "/absences", json={"player_name": "Carol", "game_id": game_id}
    )

    assert response.status_code == 400


def test_sign_up_confirms_when_there_is_room(client: TestClient) -> None:
    season = _start_season(client, member_names=["Alice"], capacity=18)
    game_id = season["games"][0]["id"]

    response = client.post(
        "/drop-ins", json={"player_name": "Carol", "game_id": game_id}
    )

    assert response.status_code == 200
    assert response.json()["status"] == "confirmed"


def test_sign_up_waitlists_once_the_game_is_full(client: TestClient) -> None:
    # capacity=1 and one member fills it before anyone else can join
    season = _start_season(client, member_names=["Alice"], capacity=1)
    game_id = season["games"][0]["id"]

    response = client.post(
        "/drop-ins", json={"player_name": "Carol", "game_id": game_id}
    )

    assert response.status_code == 200
    assert response.json()["status"] == "waitlisted"


def test_absence_promotes_the_earliest_waitlisted_player(client: TestClient) -> None:
    season = _start_season(client, member_names=["Alice"], capacity=1)
    game_id = season["games"][0]["id"]
    waitlisted = client.post(
        "/drop-ins", json={"player_name": "Carol", "game_id": game_id}
    )
    assert waitlisted.json()["status"] == "waitlisted"

    response = client.post(
        "/absences", json={"player_name": "Alice", "game_id": game_id}
    )

    assert response.json()["promoted_from_waitlist"] == waitlisted.json()["player_id"]


def test_absence_with_nobody_waiting_promotes_nobody(client: TestClient) -> None:
    season = _start_season(client, member_names=["Alice"], capacity=18)
    game_id = season["games"][0]["id"]

    response = client.post(
        "/absences", json={"player_name": "Alice", "game_id": game_id}
    )

    assert response.json()["promoted_from_waitlist"] is None


def test_cancel_marks_the_drop_in_cancelled(client: TestClient) -> None:
    season = _start_season(client, member_names=["Alice"], capacity=18)
    game_id = season["games"][0]["id"]
    signup = client.post("/drop-ins", json={"player_name": "Carol", "game_id": game_id})
    drop_in_id = signup.json()["id"]

    response = client.post(f"/drop-ins/{drop_in_id}/cancel")

    assert response.status_code == 200
    assert response.json()["cancelled_at"] is not None


def test_cancel_promotes_the_earliest_waitlisted_player(client: TestClient) -> None:
    # 1 member + capacity 2 leaves exactly one open drop-in slot.
    season = _start_season(client, member_names=["Alice"], capacity=2)
    game_id = season["games"][0]["id"]
    confirmed = client.post(
        "/drop-ins", json={"player_name": "Bob", "game_id": game_id}
    )
    waitlisted = client.post(
        "/drop-ins", json={"player_name": "Carol", "game_id": game_id}
    )
    assert confirmed.json()["status"] == "confirmed"
    assert waitlisted.json()["status"] == "waitlisted"

    response = client.post(f"/drop-ins/{confirmed.json()['id']}/cancel")

    assert response.json()["promoted_from_waitlist"] == waitlisted.json()["player_id"]


def test_cancel_rejects_an_already_cancelled_drop_in(client: TestClient) -> None:
    season = _start_season(client, member_names=["Alice"], capacity=18)
    game_id = season["games"][0]["id"]
    signup = client.post("/drop-ins", json={"player_name": "Carol", "game_id": game_id})
    drop_in_id = signup.json()["id"]
    client.post(f"/drop-ins/{drop_in_id}/cancel")

    response = client.post(f"/drop-ins/{drop_in_id}/cancel")

    assert response.status_code == 400


def test_cancel_an_unknown_drop_in_returns_404(client: TestClient) -> None:
    response = client.post("/drop-ins/999999/cancel")

    assert response.status_code == 404


def _clean_season(client: TestClient) -> dict[str, Any]:
    """total_venue_cost=10000, 8 games, 5 members -> share_per_game = 250."""
    return _start_season(
        client,
        total_venue_cost="10000",
        game_dates=[f"2026-08-{18 + i:02d}" for i in range(8)],
        member_names=["Alice", "Bob", "Carol", "Dave", "Eve"],
    )


def test_settlement_baseline_charges_full_season_fee(client: TestClient) -> None:
    season = _clean_season(client)

    response = client.get(f"/seasons/{season['id']}/settlement")

    assert response.status_code == 200
    body = response.json()
    assert len(body["members"]) == 5
    for member in body["members"]:
        assert member["season_fee"] == "2000"
        assert member["refund"] == "0"
        assert member["net"] == "-2000"


def test_settlement_refunds_a_covered_absence(client: TestClient) -> None:
    season = _clean_season(client)
    game_id = season["games"][0]["id"]
    client.post("/absences", json={"player_name": "Alice", "game_id": game_id})
    client.post("/drop-ins", json={"player_name": "Frank", "game_id": game_id})

    response = client.get(f"/seasons/{season['id']}/settlement")

    body = response.json()
    alice = next(m for m in body["members"] if m["player_name"] == "Alice")
    assert alice["refund"] == "250"
    assert alice["net"] == "-1750"


def test_settlement_for_an_unknown_season_returns_404(client: TestClient) -> None:
    response = client.get("/seasons/999999/settlement")

    assert response.status_code == 404


def test_get_season_lists_games_and_members(client: TestClient) -> None:
    season = _start_season(client, member_names=["Alice", "Bob"])

    response = client.get(f"/seasons/{season['id']}")

    assert response.status_code == 200
    body = response.json()
    assert {m["name"] for m in body["members"]} == {"Alice", "Bob"}
    assert len(body["games"]) == 2
    assert body["games"][0]["absent_player_names"] == []
    assert body["games"][0]["confirmed_drop_ins"] == []
    assert body["games"][0]["waitlist_entries"] == []


def test_get_season_reflects_absences_signups_and_waitlist(
    client: TestClient,
) -> None:
    season = _start_season(client, member_names=["Alice"], capacity=1)
    game_id = season["games"][0]["id"]
    client.post("/absences", json={"player_name": "Alice", "game_id": game_id})
    bob_signup = client.post(
        "/drop-ins", json={"player_name": "Bob", "game_id": game_id}
    )
    carol_signup = client.post(
        "/drop-ins", json={"player_name": "Carol", "game_id": game_id}
    )

    response = client.get(f"/seasons/{season['id']}")

    game = next(g for g in response.json()["games"] if g["id"] == game_id)
    assert game["absent_player_names"] == ["Alice"]
    assert game["confirmed_drop_ins"] == [
        {"id": bob_signup.json()["id"], "player_name": "Bob"}
    ]
    assert game["waitlist_entries"] == [
        {"id": carol_signup.json()["id"], "player_name": "Carol"}
    ]


def test_get_season_for_an_unknown_season_returns_404(client: TestClient) -> None:
    response = client.get("/seasons/999999")

    assert response.status_code == 404
