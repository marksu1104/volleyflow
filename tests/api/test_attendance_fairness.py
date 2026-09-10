"""One person, one place — and who is allowed to put them there.

Every case here came from playing with the real app on 2026-09-10:
a queued person named as a substitute appearing in both lists at once,
a member locked out of their own game once somebody covered for them,
and the worry that anyone could sign up on somebody else's account.
"""

from typing import Any

from fastapi.testclient import TestClient

from tests.api.factories import auth_headers, identify, start_season


def _member(client: TestClient, season: dict[str, Any], name: str) -> dict[str, Any]:
    person = identify(client, name)
    client.post(
        f"/clubs/{season['club_id']}/join", headers=auth_headers(person["token"])
    )
    return person


def _game(client: TestClient, season: dict[str, Any]) -> dict[str, Any]:
    return client.get(f"/seasons/{season['id']}").json()["games"][0]


def test_a_queued_person_named_as_a_substitute_leaves_the_queue(
    client: TestClient,
) -> None:
    # They were in both lists at once: on the court and still waiting for
    # a place on it.
    season = start_season(client, member_names=["Alice", "Bob"], capacity=2)
    game_id = season["games"][0]["id"]
    queued = client.post(
        "/drop-ins", json={"player_name": "Carol", "game_id": game_id}
    ).json()
    assert queued["status"] == "waitlisted"
    absence = client.post(
        "/absences", json={"player_name": "Alice", "game_id": game_id}
    ).json()

    client.put(f"/absences/{absence['id']}/substitute", json={"player_name": "Carol"})

    game = _game(client, season)
    playing = [d["player_name"] for d in game["confirmed_drop_ins"]]
    waiting = [w["player_name"] for w in game["waitlist_entries"]]
    assert "Carol" in playing
    assert "Carol" not in waiting, "one person cannot hold a place and want one"


def test_a_member_can_take_their_place_back_and_the_stand_in_waits_again(
    client: TestClient,
) -> None:
    season = start_season(client, member_names=["Alice"], capacity=1)
    game_id = season["games"][0]["id"]
    absence = client.post(
        "/absences", json={"player_name": "Alice", "game_id": game_id}
    ).json()
    carol = client.post(
        "/drop-ins", json={"player_name": "Carol", "game_id": game_id}
    ).json()

    response = client.post(f"/absences/{absence['id']}/cancel")

    assert response.status_code == 200
    game = _game(client, season)
    assert game["confirmed_drop_ins"] == []
    assert [w["player_name"] for w in game["waitlist_entries"]] == ["Carol"]
    assert response.json()["released_player_id"] == carol["player_id"]


def test_taking_a_place_back_releases_the_substitute_that_was_arranged(
    client: TestClient,
) -> None:
    # The member arranged them, so the member may un-arrange them.
    season = start_season(client, member_names=["Alice"], capacity=1)
    game_id = season["games"][0]["id"]
    absence = client.post(
        "/absences", json={"player_name": "Alice", "game_id": game_id}
    ).json()
    zoe = client.put(
        f"/absences/{absence['id']}/substitute", json={"player_name": "Zoe"}
    ).json()

    response = client.post(f"/absences/{absence['id']}/cancel")

    assert response.json()["released_player_id"] == zoe["player_id"]
    assert _game(client, season)["confirmed_drop_ins"] == []


def test_taking_a_place_back_never_releases_somebody_elses_substitute(
    client: TestClient,
) -> None:
    # Bob's 代打 is filling Bob's slot, not Alice's. Alice returning has
    # nothing to do with them.
    season = start_season(client, member_names=["Alice", "Bob"], capacity=2)
    game_id = season["games"][0]["id"]
    bobs = client.post(
        "/absences", json={"player_name": "Bob", "game_id": game_id}
    ).json()
    client.put(f"/absences/{bobs['id']}/substitute", json={"player_name": "Zoe"})
    alices = client.post(
        "/absences", json={"player_name": "Alice", "game_id": game_id}
    ).json()

    client.post(f"/absences/{alices['id']}/cancel")

    playing = [d["player_name"] for d in _game(client, season)["confirmed_drop_ins"]]
    assert playing == ["Zoe"], "Bob's arrangement is untouched"


def test_nobody_may_sign_up_on_an_account_that_is_not_theirs(
    client: TestClient,
) -> None:
    """Somebody with a LINE account speaks for themselves.

    A guest typed in by hand has no account and never will, so a member
    signing them up is the only way they can play. Somebody who *has*
    logged in is different: putting them on a roster in their name — and
    on the hook for the fee — is theirs to do, or the organizer's.
    """
    season = start_season(client, member_names=["Alice"], capacity=18)
    game_id = season["games"][0]["id"]
    impostor = _member(client, season, "Impostor")
    victim = _member(client, season, "Victim")

    response = client.post(
        f"/games/{game_id}/drop-ins",
        json={"people": [{"player_name": victim["name"], "player_id": victim["id"]}]},
        headers=auth_headers(impostor["token"]),
    )

    assert response.status_code == 403


def test_a_member_may_still_bring_a_guest_with_no_account(
    client: TestClient,
) -> None:
    season = start_season(client, member_names=["Alice"], capacity=18)
    game_id = season["games"][0]["id"]
    host = _member(client, season, "Host")

    response = client.post(
        f"/games/{game_id}/drop-ins",
        json={"people": [{"player_name": "Host's friend", "gender": "male"}]},
        headers=auth_headers(host["token"]),
    )

    assert response.status_code == 200


def test_a_substitute_may_not_be_somebody_elses_account_either(
    client: TestClient,
) -> None:
    # 指定代打 creates a signup like any other, so the same rule holds:
    # naming somebody who has an account puts them on the hook for a fee
    # they never agreed to.
    season = start_season(client, member_names=["Alice"], capacity=18)
    game_id = season["games"][0]["id"]
    victim = _member(client, season, "Victim")
    absence = client.post(
        "/absences", json={"player_name": "Alice", "game_id": game_id}
    ).json()
    alice = _member(client, season, "Alice's account")
    client.post(
        f"/clubs/{season['club_id']}/players/{season['member_ids'][0]}/link",
        json={"line_player_id": alice["id"]},
    )

    response = client.put(
        f"/absences/{absence['id']}/substitute",
        json={"player_name": victim["name"]},
        headers=auth_headers(alice["token"]),
    )

    assert response.status_code == 403
