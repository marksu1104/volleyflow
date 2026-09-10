"""Naming a substitute for a slot the waitlist has already filled.

Reported from real use on 2026-09-10: "I took leave, somebody was
promoted into my place, and then setting my substitute didn't give them
my place — it added a nineteenth person." Recording an absence offers the
slot to the queue immediately (CLAUDE.md 2.3), so this collision is the
normal case, not an edge one.
"""

from typing import Any

from fastapi.testclient import TestClient

from tests.api.factories import start_season


def _full_game(client: TestClient) -> tuple[dict[str, Any], int]:
    """Alice and Bob on a roster of two, which is also the capacity."""
    season = start_season(client, member_names=["Alice", "Bob"], capacity=2)
    return season, season["games"][0]["id"]


def _playing(client: TestClient, season: dict[str, Any]) -> list[str]:
    """Who will be on court: members who haven't taken leave, plus every
    confirmed drop-in. The same sum the app shows."""
    detail = client.get(f"/seasons/{season['id']}").json()
    game = detail["games"][0]
    absent = {a["player_name"] for a in game["absences"]}
    members = [m["name"] for m in detail["members"] if m["name"] not in absent]
    return members + [d["player_name"] for d in game["confirmed_drop_ins"]]


def test_a_substitute_takes_the_slot_rather_than_making_a_nineteenth_player(
    client: TestClient,
) -> None:
    season, game_id = _full_game(client)
    queued = client.post(
        "/drop-ins", json={"player_name": "Carol", "game_id": game_id}
    ).json()
    assert queued["status"] == "waitlisted"
    absence = client.post(
        "/absences", json={"player_name": "Alice", "game_id": game_id}
    ).json()
    assert absence["promoted_from_waitlist"] is not None, "Carol took the empty slot"

    response = client.put(
        f"/absences/{absence['id']}/substitute", json={"player_name": "Dave"}
    )

    assert response.status_code == 200
    playing = _playing(client, season)
    assert sorted(playing) == ["Bob", "Dave"], "two people on a two-person court"
    assert len(playing) == season["capacity"]


def test_the_person_bumped_is_named_and_put_back_in_the_queue(
    client: TestClient,
) -> None:
    # They must not simply vanish: they signed up, they were charged, and
    # they are still waiting for a place.
    season, game_id = _full_game(client)
    carol = client.post(
        "/drop-ins", json={"player_name": "Carol", "game_id": game_id}
    ).json()
    absence = client.post(
        "/absences", json={"player_name": "Alice", "game_id": game_id}
    ).json()

    response = client.put(
        f"/absences/{absence['id']}/substitute", json={"player_name": "Dave"}
    )

    assert response.json()["displaced_player_id"] == carol["player_id"]
    game = client.get(f"/seasons/{season['id']}").json()["games"][0]
    assert [w["player_name"] for w in game["waitlist_entries"]] == ["Carol"]
    ledger = client.get(
        f"/clubs/{season['club_id']}/players/{carol['player_id']}/ledger"
    ).json()
    assert ledger["balance"] == "0", "charged on promotion, refunded on being bumped"


def test_a_bumped_player_keeps_their_place_ahead_of_later_arrivals(
    client: TestClient,
) -> None:
    season, game_id = _full_game(client)
    carol = client.post(
        "/drop-ins", json={"player_name": "Carol", "game_id": game_id}
    ).json()
    client.post("/drop-ins", json={"player_name": "Erin", "game_id": game_id})
    absence = client.post(
        "/absences", json={"player_name": "Alice", "game_id": game_id}
    ).json()

    client.put(f"/absences/{absence['id']}/substitute", json={"player_name": "Dave"})

    game = client.get(f"/seasons/{season['id']}").json()["games"][0]
    assert [w["player_name"] for w in game["waitlist_entries"]] == ["Carol", "Erin"], (
        "Carol queued first and being bumped must not send her to the back"
    )
    assert carol["status"] == "waitlisted"


def test_nobody_is_bumped_when_the_slot_is_still_open(client: TestClient) -> None:
    # The ordinary case: no queue, so the absence left a real gap and the
    # substitute simply fills it.
    season, game_id = _full_game(client)
    absence = client.post(
        "/absences", json={"player_name": "Alice", "game_id": game_id}
    ).json()

    response = client.put(
        f"/absences/{absence['id']}/substitute", json={"player_name": "Dave"}
    )

    assert response.json()["displaced_player_id"] is None
    assert sorted(_playing(client, season)) == ["Bob", "Dave"]


def test_somebody_elses_named_substitute_is_never_bumped(client: TestClient) -> None:
    # A personally arranged substitute was chosen exactly the way this one
    # is being chosen; picking them to displace would just move the
    # problem onto another member.
    season = start_season(client, member_names=["Alice", "Bob"], capacity=2)
    game_id = season["games"][0]["id"]
    bobs_absence = client.post(
        "/absences", json={"player_name": "Bob", "game_id": game_id}
    ).json()
    client.put(
        f"/absences/{bobs_absence['id']}/substitute", json={"player_name": "Zoe"}
    )
    alices_absence = client.post(
        "/absences", json={"player_name": "Alice", "game_id": game_id}
    ).json()

    response = client.put(
        f"/absences/{alices_absence['id']}/substitute", json={"player_name": "Dave"}
    )

    assert response.status_code == 200
    playing = _playing(client, season)
    assert sorted(playing) == ["Dave", "Zoe"], "both slots covered by their own pick"


def test_a_full_game_of_named_substitutes_refuses_rather_than_overfilling(
    client: TestClient,
) -> None:
    season = start_season(client, member_names=["Alice"], capacity=1)
    game_id = season["games"][0]["id"]
    absence = client.post(
        "/absences", json={"player_name": "Alice", "game_id": game_id}
    ).json()
    client.put(f"/absences/{absence['id']}/substitute", json={"player_name": "Zoe"})
    # Zoe now holds the only slot, as Alice's own pick. A second member's
    # absence has nowhere to put a substitute.
    client.post(f"/seasons/{season['id']}/members", json={"player_name": "Bob"})
    client.patch(f"/seasons/{season['id']}", json={"capacity": 1})
    bobs_absence = client.post(
        "/absences", json={"player_name": "Bob", "game_id": game_id}
    ).json()

    response = client.put(
        f"/absences/{bobs_absence['id']}/substitute", json={"player_name": "Dave"}
    )

    assert response.status_code == 400
    assert "full" in response.json()["detail"]
