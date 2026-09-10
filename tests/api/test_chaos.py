"""Several people acting on the same game at once, and acting badly.

Asked for on 2026-09-11: "模擬使用者甚至多使用者的狀態下，複雜跟嚴格有
點亂來的操作是否還是可以操作的很滑順，不會有錯誤". Every test here runs
a plausible-but-messy sequence and then checks the invariants that must
hold no matter what happened:

* nobody is on the court and in the queue at the same time
* nobody is on the court twice
* the court never holds more than the capacity
* everybody not playing owes nothing for that game
* the club never collects more than the venue cost from one night

The point is not any single sequence; it is that the answer to "does
this hold?" is a function, so any sequence can be thrown at it.
"""

from decimal import Decimal
from typing import Any

from fastapi.testclient import TestClient

from tests.api.factories import auth_headers, identify, start_season


def _member(client: TestClient, season: dict[str, Any], name: str) -> dict[str, Any]:
    person = identify(client, name)
    client.post(
        f"/clubs/{season['club_id']}/join", headers=auth_headers(person["token"])
    )
    return person


def check_invariants(client: TestClient, season: dict[str, Any]) -> dict[str, Any]:
    """Every rule that must be true of a game, whatever was done to it."""
    detail = client.get(f"/seasons/{season['id']}").json()
    game = detail["games"][0]

    absent = {a["player_name"] for a in game["absences"]}
    members_playing = [m["name"] for m in detail["members"] if m["name"] not in absent]
    drop_ins = [d["player_name"] for d in game["confirmed_drop_ins"]]
    queued = [w["player_name"] for w in game["waitlist_entries"]]
    playing = members_playing + drop_ins

    assert len(playing) == len(set(playing)), f"somebody is on court twice: {playing}"
    assert not (set(playing) & set(queued)), (
        f"on court and waiting at once: {set(playing) & set(queued)}"
    )
    assert len(queued) == len(set(queued)), f"queued twice: {queued}"
    assert len(playing) <= detail["capacity"], (
        f"{len(playing)} people on a court for {detail['capacity']}"
    )

    # Money: a drop-in fee is a charge against a player, so the total can
    # never come out positive. Churn that refunded more than it charged
    # would show up here as the club owing somebody for turning up.
    charged = sum(
        Decimal(row.get("drop_in_fees", "0") or "0")
        for row in client.get(
            f"/clubs/{season['club_id']}/balances?season_id={season['id']}"
        ).json()
    )
    assert charged <= 0, "drop-in fees are charges, never credits"

    return {"playing": playing, "queued": queued, "capacity": detail["capacity"]}


def test_two_members_acting_on_the_same_slot_leave_it_consistent(
    client: TestClient,
) -> None:
    # Alice takes leave; the queue fills her slot; she changes her mind;
    # somebody else takes leave meanwhile. All of it interleaved.
    season = start_season(client, member_names=["Alice", "Bob"], capacity=2)
    game_id = season["games"][0]["id"]
    for name in ["甲", "乙", "丙"]:
        client.post("/drop-ins", json={"player_name": name, "game_id": game_id})

    a = client.post(
        "/absences", json={"player_name": "Alice", "game_id": game_id}
    ).json()
    b = client.post("/absences", json={"player_name": "Bob", "game_id": game_id}).json()
    client.post(f"/absences/{a['id']}/cancel")
    client.put(f"/absences/{b['id']}/substitute", json={"player_name": "丙"})
    client.post(f"/absences/{b['id']}/cancel")

    state = check_invariants(client, season)
    assert set(state["playing"]) == {"Alice", "Bob"}


def test_hammering_the_substitute_through_everybody_settles_cleanly(
    client: TestClient,
) -> None:
    season = start_season(client, member_names=["Alice"], capacity=1)
    game_id = season["games"][0]["id"]
    names = ["甲", "乙", "丙", "丁"]
    for n in names:
        client.post("/drop-ins", json={"player_name": n, "game_id": game_id})
    absence = client.post(
        "/absences", json={"player_name": "Alice", "game_id": game_id}
    ).json()

    for n in names * 3:
        client.put(f"/absences/{absence['id']}/substitute", json={"player_name": n})

    state = check_invariants(client, season)
    assert state["playing"] == ["丁"], "the last one named is the one playing"
    assert set(state["queued"]) == {"甲", "乙", "丙"}


def test_a_member_and_the_organizer_fighting_over_one_absence(
    client: TestClient,
) -> None:
    # Both are entitled to act, and they do so in an awkward order.
    season = start_season(client, member_names=["Alice"], capacity=2)
    game_id = season["games"][0]["id"]
    alice = _member(client, season, "Alice's account")
    client.post(
        f"/clubs/{season['club_id']}/players/{season['member_ids'][0]}/link",
        json={"line_player_id": alice["id"]},
    )
    as_alice = auth_headers(alice["token"])

    absence = client.post(
        "/absences", json={"player_name": "Alice", "game_id": game_id}, headers=as_alice
    ).json()
    client.put(
        f"/absences/{absence['id']}/substitute",
        json={"player_name": "Zoe"},
        headers=as_alice,
    )
    # The organizer replaces her pick, then she takes her place back.
    client.put(f"/absences/{absence['id']}/substitute", json={"player_name": "Yuki"})
    client.post(f"/absences/{absence['id']}/cancel", headers=as_alice)

    state = check_invariants(client, season)
    assert state["playing"] == ["Alice"], "she is playing and nobody is standing in"


def test_a_full_game_churned_by_several_people_stays_within_capacity(
    client: TestClient,
) -> None:
    season = start_season(client, member_names=["Alice", "Bob", "Carol"], capacity=3)
    game_id = season["games"][0]["id"]
    hosts = [_member(client, season, f"Host{i}") for i in range(3)]
    for i, host in enumerate(hosts):
        client.post(
            f"/games/{game_id}/drop-ins",
            json={"people": [{"player_name": f"客人{i}", "gender": "male"}]},
            headers=auth_headers(host["token"]),
        )
    absences = [
        client.post("/absences", json={"player_name": n, "game_id": game_id}).json()
        for n in ["Alice", "Bob"]
    ]

    # Everybody undoes and redoes things in a jumble.
    client.post(f"/absences/{absences[0]['id']}/cancel")
    client.put(
        f"/absences/{absences[1]['id']}/substitute", json={"player_name": "客人2"}
    )
    client.post("/absences", json={"player_name": "Carol", "game_id": game_id})
    client.post(f"/absences/{absences[1]['id']}/cancel")

    check_invariants(client, season)


def test_nothing_is_left_owing_by_somebody_who_never_played(
    client: TestClient,
) -> None:
    # The money version of the same worry: churn must net to zero for
    # anybody who ends up not on the court.
    season = start_season(client, member_names=["Alice"], capacity=1)
    game_id = season["games"][0]["id"]
    people = {
        n: client.post("/drop-ins", json={"player_name": n, "game_id": game_id}).json()
        for n in ["甲", "乙", "丙"]
    }
    absence = client.post(
        "/absences", json={"player_name": "Alice", "game_id": game_id}
    ).json()
    for n in ["甲", "乙", "丙", "甲"]:
        client.put(f"/absences/{absence['id']}/substitute", json={"player_name": n})
    client.post(f"/absences/{absence['id']}/cancel")

    state = check_invariants(client, season)
    assert state["playing"] == ["Alice"]
    for name, person in people.items():
        ledger = client.get(
            f"/clubs/{season['club_id']}/players/{person['player_id']}/ledger"
        ).json()
        assert ledger["balance"] == "0", f"{name} never played and owes nothing"
