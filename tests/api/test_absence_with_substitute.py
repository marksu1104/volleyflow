"""Taking leave and naming the substitute in one request.

Two requests — record the absence, then name the substitute — hand the
slot to the queue in between: whoever queued first is promoted, charged,
then bumped back out and refunded the moment the substitute is named. Sent
together, the slot goes straight to the substitute and the queue never
sees it.
"""

from typing import Any

from fastapi.testclient import TestClient

from tests.api.factories import start_season


def _full_game(client: TestClient) -> tuple[dict[str, Any], int]:
    """Alice and Bob on a roster of two, which is also the capacity."""
    season = start_season(client, member_names=["Alice", "Bob"], capacity=2)
    return season, season["games"][0]["id"]


def _game(client: TestClient, season: dict[str, Any]) -> dict[str, Any]:
    game: dict[str, Any] = client.get(f"/seasons/{season['id']}").json()["games"][0]
    return game


def test_a_substitute_named_with_the_absence_takes_the_slot_before_the_queue(
    client: TestClient,
) -> None:
    season, game_id = _full_game(client)
    client.post("/drop-ins", json={"player_name": "Carol", "game_id": game_id})

    response = client.post(
        "/absences",
        json={
            "player_name": "Alice",
            "game_id": game_id,
            "substitute": {"player_name": "Dave", "gender": "male"},
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["promoted_from_waitlist"] is None
    assert body["substitute"]["status"] == "confirmed"
    assert body["substitute"]["displaced_player_id"] is None
    game = _game(client, season)
    assert [d["player_name"] for d in game["confirmed_drop_ins"]] == ["Dave"]
    assert [w["player_name"] for w in game["waitlist_entries"]] == ["Carol"]


def test_the_queue_head_is_never_charged_for_a_slot_they_never_had(
    client: TestClient,
) -> None:
    season, game_id = _full_game(client)
    carol = client.post(
        "/drop-ins", json={"player_name": "Carol", "game_id": game_id}
    ).json()

    client.post(
        "/absences",
        json={
            "player_name": "Alice",
            "game_id": game_id,
            "substitute": {"player_name": "Dave"},
        },
    )

    ledger = client.get(
        f"/clubs/{season['club_id']}/players/{carol['player_id']}/ledger"
    ).json()
    assert ledger["entries"] == [], "no charge and refund pair on Carol's ledger"


def test_the_substitute_is_the_absent_members_own_arrangement(
    client: TestClient,
) -> None:
    season, game_id = _full_game(client)

    client.post(
        "/absences",
        json={
            "player_name": "Alice",
            "game_id": game_id,
            "substitute": {"player_name": "Dave"},
        },
    )

    game = _game(client, season)
    assert game["absences"][0]["covered_by"] == "Dave"
    assert game["confirmed_drop_ins"][0]["covering"] == "Alice"


def test_a_queued_person_named_with_the_absence_leaves_the_queue(
    client: TestClient,
) -> None:
    season, game_id = _full_game(client)
    client.post("/drop-ins", json={"player_name": "Carol", "game_id": game_id})
    client.post("/drop-ins", json={"player_name": "Erin", "game_id": game_id})

    client.post(
        "/absences",
        json={
            "player_name": "Alice",
            "game_id": game_id,
            "substitute": {"player_name": "Erin"},
        },
    )

    game = _game(client, season)
    assert [d["player_name"] for d in game["confirmed_drop_ins"]] == ["Erin"]
    assert [w["player_name"] for w in game["waitlist_entries"]] == ["Carol"]


def test_a_refused_substitute_leaves_no_absence_behind(client: TestClient) -> None:
    # One request is one decision: an absence the member never meant to
    # take without that substitute must not survive the refusal.
    season, game_id = _full_game(client)
    client.post("/drop-ins", json={"player_name": "Carol", "game_id": game_id})

    response = client.post(
        "/absences",
        json={
            "player_name": "Alice",
            "game_id": game_id,
            "substitute": {"player_name": "Bob"},
        },
    )

    assert response.status_code == 400
    game = _game(client, season)
    assert game["absences"] == []
    assert [w["player_name"] for w in game["waitlist_entries"]] == ["Carol"]
