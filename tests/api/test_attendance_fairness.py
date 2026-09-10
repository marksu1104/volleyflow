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


def test_cancelling_an_arranged_substitute_returns_them_to_their_place_in_the_queue(
    client: TestClient,
) -> None:
    # Reported from real use: picking the third person in the queue as
    # your 代打 and then cancelling deleted them from the game. They had
    # given up their queue place to take the slot and got nothing back.
    season = start_season(client, member_names=["Alice"], capacity=1)
    game_id = season["games"][0]["id"]
    first = client.post(
        "/drop-ins", json={"player_name": "第一位", "game_id": game_id}
    ).json()
    client.post("/drop-ins", json={"player_name": "第二位", "game_id": game_id})
    third = client.post(
        "/drop-ins", json={"player_name": "第三位", "game_id": game_id}
    ).json()
    absence = client.post(
        "/absences", json={"player_name": "Alice", "game_id": game_id}
    ).json()
    # Alice picks the third in line rather than whoever the queue offers.
    substitute = client.put(
        f"/absences/{absence['id']}/substitute", json={"player_name": "第三位"}
    ).json()
    assert substitute["player_id"] == third["player_id"]

    client.post(f"/drop-ins/{substitute['id']}/cancel")

    game = _game(client, season)
    playing = [d["player_name"] for d in game["confirmed_drop_ins"]]
    waiting = [w["player_name"] for w in game["waitlist_entries"]]
    assert playing == ["第一位"], "the freed slot goes to whoever is genuinely first"
    assert waiting == ["第二位", "第三位"], "and the third keeps their own place"
    assert first["status"] == "waitlisted"


def test_replacing_a_substitute_returns_the_replaced_one_to_the_queue(
    client: TestClient,
) -> None:
    # Reported as "換來換去候補會不見". Cancelling a substitute gave the
    # queue place back; replacing one with somebody else did not, and
    # the person replaced was deleted from the game.
    season = start_season(client, member_names=["Alice"], capacity=1)
    game_id = season["games"][0]["id"]
    first = client.post(
        "/drop-ins", json={"player_name": "第一位", "game_id": game_id}
    ).json()
    second = client.post(
        "/drop-ins", json={"player_name": "第二位", "game_id": game_id}
    ).json()
    absence = client.post(
        "/absences", json={"player_name": "Alice", "game_id": game_id}
    ).json()
    client.put(f"/absences/{absence['id']}/substitute", json={"player_name": "第一位"})

    client.put(f"/absences/{absence['id']}/substitute", json={"player_name": "第二位"})

    game = _game(client, season)
    assert [d["player_name"] for d in game["confirmed_drop_ins"]] == ["第二位"]
    assert [w["player_name"] for w in game["waitlist_entries"]] == ["第一位"], (
        "the one replaced goes back to waiting, not away"
    )
    assert first["status"] == "waitlisted" and second["status"] == "waitlisted"


def test_swapping_substitutes_repeatedly_keeps_everyone_accounted_for(
    client: TestClient,
) -> None:
    # The exact thing that was being done by hand: cycle the substitute
    # through every queued person in turn. Nobody may fall out along the
    # way, and the money must come back to zero for everyone not playing.
    season = start_season(client, member_names=["Alice"], capacity=1)
    game_id = season["games"][0]["id"]
    names = ["甲", "乙", "丙"]
    queued = {
        n: client.post("/drop-ins", json={"player_name": n, "game_id": game_id}).json()
        for n in names
    }
    absence = client.post(
        "/absences", json={"player_name": "Alice", "game_id": game_id}
    ).json()

    for n in names + ["甲", "丙"]:
        client.put(f"/absences/{absence['id']}/substitute", json={"player_name": n})

    game = _game(client, season)
    playing = [d["player_name"] for d in game["confirmed_drop_ins"]]
    waiting = [w["player_name"] for w in game["waitlist_entries"]]
    assert playing == ["丙"]
    assert set(waiting) == {"甲", "乙"}, "everybody is still somewhere"
    for n in ["甲", "乙"]:
        ledger = client.get(
            f"/clubs/{season['club_id']}/players/{queued[n]['player_id']}/ledger"
        ).json()
        assert ledger["balance"] == "0", f"{n} is not playing and owes nothing"


def test_a_drop_in_who_cancels_themselves_does_not_rejoin_the_queue(
    client: TestClient,
) -> None:
    # The other half of the rule: leaving is a choice, and choosing to
    # leave must not put you straight back in line.
    season = start_season(client, member_names=["Alice"], capacity=18)
    game_id = season["games"][0]["id"]
    carol = _member(client, season, "Carol")
    signup = client.post(
        f"/games/{game_id}/drop-ins",
        json={"people": [{"player_name": carol["name"], "player_id": carol["id"]}]},
        headers=auth_headers(carol["token"]),
    ).json()["results"][0]

    client.post(
        f"/drop-ins/{signup['id']}/cancel", headers=auth_headers(carol["token"])
    )

    game = _game(client, season)
    assert game["confirmed_drop_ins"] == []
    assert game["waitlist_entries"] == []


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
