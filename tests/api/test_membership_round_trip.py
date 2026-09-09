"""Adding somebody to the fixed roster and taking them off again.

Reported from real use: "I added a member to the season roster and then
removed them, and their past drop-in record had disappeared." Written to
find out whether that is true before deciding what to do about it.
"""

from typing import Any

from fastapi.testclient import TestClient

from tests.api.factories import start_season


def _ledger_total(client: TestClient, season: dict[str, Any], player_id: int) -> float:
    body = client.get(f"/clubs/{season['club_id']}/players/{player_id}/ledger").json()
    return float(body["balance"])


def test_a_drop_in_survives_being_made_a_member_and_unmade(
    client: TestClient,
) -> None:
    season = start_season(client, member_names=["Alice"], capacity=18)
    game = season["games"][0]
    carol = client.post(
        "/drop-ins", json={"player_name": "Carol", "game_id": game["id"]}
    ).json()
    assert carol["status"] == "confirmed"
    owed_as_drop_in = _ledger_total(client, season, carol["player_id"])
    assert owed_as_drop_in != 0, "she played one game and was charged for it"

    client.post(f"/seasons/{season['id']}/members", json={"player_name": "Carol"})
    client.delete(f"/seasons/{season['id']}/members/{carol['player_id']}")

    playing = client.get(f"/seasons/{season['id']}").json()["games"][0][
        "confirmed_drop_ins"
    ]
    assert [p["player_name"] for p in playing] == ["Carol"], (
        "she was on court that night; taking her off the roster cannot "
        "erase that she played"
    )
    assert _ledger_total(client, season, carol["player_id"]) == owed_as_drop_in, (
        "and she still owes for the game she played"
    )


def test_a_signup_the_player_cancelled_themselves_stays_cancelled(
    client: TestClient,
) -> None:
    # The other half of the same fix. Both kinds of cancellation look
    # identical in cancelled_at, and putting this one back would drop
    # somebody onto a roster they had deliberately left.
    season = start_season(client, member_names=["Alice"], capacity=18)
    game = season["games"][0]
    carol = client.post(
        "/drop-ins", json={"player_name": "Carol", "game_id": game["id"]}
    ).json()
    client.post(f"/drop-ins/{carol['id']}/cancel")

    client.post(f"/seasons/{season['id']}/members", json={"player_name": "Carol"})
    client.delete(f"/seasons/{season['id']}/members/{carol['player_id']}")

    playing = client.get(f"/seasons/{season['id']}").json()["games"][0][
        "confirmed_drop_ins"
    ]
    assert playing == []
    assert _ledger_total(client, season, carol["player_id"]) == 0, (
        "she cancelled, so she owes nothing"
    )


def test_a_member_is_not_billed_twice_for_a_night_they_signed_up_for(
    client: TestClient,
) -> None:
    # What the absorption is for in the first place — guarded here so
    # the restore can't be "fixed" by simply not absorbing.
    season = start_season(
        client, member_names=["Alice"], capacity=18, total_venue_cost="1000"
    )
    game = season["games"][0]
    carol = client.post(
        "/drop-ins", json={"player_name": "Carol", "game_id": game["id"]}
    ).json()

    client.post(f"/seasons/{season['id']}/members", json={"player_name": "Carol"})

    entries = client.get(
        f"/clubs/{season['club_id']}/players/{carol['player_id']}/ledger"
    ).json()["entries"]
    drop_in_total = sum(
        float(e["amount"]) for e in entries if e["entry_type"] == "drop_in_fee_charged"
    )
    assert drop_in_total == 0, "the drop-in fee was handed back in full"
    playing = client.get(f"/seasons/{season['id']}").json()["games"][0][
        "confirmed_drop_ins"
    ]
    assert playing == [], "she attends as a member now, not as a drop-in"


def test_going_on_and_off_the_roster_twice_lands_in_the_same_place(
    client: TestClient,
) -> None:
    # Absorption sets the marker and the restore clears it, so a second
    # round trip has to behave exactly like the first rather than
    # doubling anything up.
    season = start_season(client, member_names=["Alice"], capacity=18)
    game = season["games"][0]
    carol = client.post(
        "/drop-ins", json={"player_name": "Carol", "game_id": game["id"]}
    ).json()
    owed = _ledger_total(client, season, carol["player_id"])

    for _ in range(2):
        client.post(f"/seasons/{season['id']}/members", json={"player_name": "Carol"})
        client.delete(f"/seasons/{season['id']}/members/{carol['player_id']}")

    assert _ledger_total(client, season, carol["player_id"]) == owed
    playing = client.get(f"/seasons/{season['id']}").json()["games"][0][
        "confirmed_drop_ins"
    ]
    assert [p["player_name"] for p in playing] == ["Carol"]
