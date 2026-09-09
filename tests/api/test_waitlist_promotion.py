"""The organizer choosing who comes off the waitlist.

Automatic promotion is first-queued-first and stays that way. This is
the manual override, and the thing worth guarding is the money: the
whole reason it exists as one request is that doing it as several
charges and refunds every person the queue steps through on the way to
the right one.
"""

from typing import Any

from fastapi.testclient import TestClient

from tests.api.factories import auth_headers, identify, start_season


def _full_game_with_queue(
    client: TestClient, queued: list[str]
) -> tuple[dict[str, Any], int, list[dict[str, Any]]]:
    """A game with capacity 1, filled by Alice, and `queued` waiting."""
    season = start_season(client, member_names=["Alice"], capacity=1)
    game_id = season["games"][0]["id"]
    entries = []
    for name in queued:
        response = client.post(
            "/drop-ins", json={"player_name": name, "game_id": game_id}
        )
        assert response.json()["status"] == "waitlisted"
        entries.append(response.json())
    return season, game_id, entries


def _ledger(client: TestClient, season: dict[str, Any], player_id: int) -> list[Any]:
    entries: list[Any] = client.get(
        f"/clubs/{season['club_id']}/players/{player_id}/ledger"
    ).json()["entries"]
    return entries


def test_a_swap_puts_the_chosen_person_on_and_takes_the_named_one_off(
    client: TestClient,
) -> None:
    season, game_id, [carol, dave] = _full_game_with_queue(client, ["Carol", "Dave"])
    promoted = client.post(
        "/absences", json={"player_name": "Alice", "game_id": game_id}
    ).json()
    assert promoted["promoted_from_waitlist"] == carol["player_id"]
    carols_drop_in = client.get(f"/seasons/{season['id']}").json()["games"][0][
        "confirmed_drop_ins"
    ][0]

    response = client.post(
        f"/waitlist/{dave['id']}/promote",
        json={"replacing_drop_in_id": carols_drop_in["id"]},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["player_id"] == dave["player_id"]
    assert body["replaced_player_id"] == carol["player_id"]
    playing = client.get(f"/seasons/{season['id']}").json()["games"][0][
        "confirmed_drop_ins"
    ]
    assert [p["player_name"] for p in playing] == ["Dave"]


def test_a_swap_moves_money_for_the_two_people_swapping_and_nobody_else(
    client: TestClient,
) -> None:
    # The reason this is one request rather than a cancel followed by a
    # promote: cancelling Carol would auto-promote Dave's *queue
    # neighbour* first, charging and refunding somebody who never played.
    season, game_id, [carol, eve, dave] = _full_game_with_queue(
        client, ["Carol", "Eve", "Dave"]
    )
    client.post("/absences", json={"player_name": "Alice", "game_id": game_id})
    carols_drop_in = client.get(f"/seasons/{season['id']}").json()["games"][0][
        "confirmed_drop_ins"
    ][0]
    eve_before = _ledger(client, season, eve["player_id"])

    client.post(
        f"/waitlist/{dave['id']}/promote",
        json={"replacing_drop_in_id": carols_drop_in["id"]},
    )

    assert _ledger(client, season, eve["player_id"]) == eve_before, (
        "Eve was never on the court and her ledger must not have moved"
    )
    carol_amounts = [e["amount"] for e in _ledger(client, season, carol["player_id"])]
    assert sum(float(a) for a in carol_amounts) == 0, "charged, then refunded in full"
    # A charge is negative — what the player owes the club.
    assert float(_ledger(client, season, dave["player_id"])[-1]["amount"]) < 0


def test_promoting_into_a_slot_that_is_genuinely_open_needs_no_swap(
    client: TestClient,
) -> None:
    # Capacity was raised, or the queue formed before somebody dropped
    # out. Nobody has to come off.
    season = start_season(client, member_names=["Alice"], capacity=1)
    game_id = season["games"][0]["id"]
    carol = client.post(
        "/drop-ins", json={"player_name": "Carol", "game_id": game_id}
    ).json()
    assert carol["status"] == "waitlisted"
    client.patch(f"/seasons/{season['id']}", json={"capacity": 2})

    response = client.post(f"/waitlist/{carol['id']}/promote", json={})

    assert response.status_code == 200
    assert response.json()["replaced_player_id"] is None


def test_a_full_game_refuses_a_promotion_that_names_nobody_to_replace(
    client: TestClient,
) -> None:
    # The capacity cap is a rule, not a suggestion — this is the decision
    # recorded on 2026-09-10: warn clearly rather than quietly overfill.
    _, game_id, [carol] = _full_game_with_queue(client, ["Carol"])

    response = client.post(f"/waitlist/{carol['id']}/promote", json={})

    assert response.status_code == 400
    assert "full" in response.json()["detail"]


def test_an_ordinary_member_cannot_reorder_the_queue(client: TestClient) -> None:
    season, game_id, [carol] = _full_game_with_queue(client, ["Carol"])
    member = identify(client, "Bystander")
    client.post(
        f"/clubs/{season['club_id']}/join", headers=auth_headers(member["token"])
    )

    response = client.post(
        f"/waitlist/{carol['id']}/promote",
        json={},
        headers=auth_headers(member["token"]),
    )

    assert response.status_code == 403


def test_replacing_a_drop_in_from_a_different_game_is_refused(
    client: TestClient,
) -> None:
    season, game_id, [carol] = _full_game_with_queue(client, ["Carol"])
    other_game_id = season["games"][1]["id"]
    elsewhere = client.post(
        "/drop-ins", json={"player_name": "Frank", "game_id": other_game_id}
    ).json()

    response = client.post(
        f"/waitlist/{carol['id']}/promote",
        json={"replacing_drop_in_id": elsewhere["id"]},
    )

    assert response.status_code == 404


def test_replacing_somebody_already_cancelled_is_refused(client: TestClient) -> None:
    # Three deep, because cancelling the person on court opens the slot
    # and the automatic rule immediately fills it with whoever is next.
    # That is the behaviour this endpoint exists to work around, and it
    # means the test needs somebody still queued behind them.
    season, game_id, [carol, _dave, eve] = _full_game_with_queue(
        client, ["Carol", "Dave", "Eve"]
    )
    client.post("/absences", json={"player_name": "Alice", "game_id": game_id})
    carols_drop_in = client.get(f"/seasons/{season['id']}").json()["games"][0][
        "confirmed_drop_ins"
    ][0]
    client.post(f"/drop-ins/{carols_drop_in['id']}/cancel")  # Dave promoted here

    response = client.post(
        f"/waitlist/{eve['id']}/promote",
        json={"replacing_drop_in_id": carols_drop_in["id"]},
    )

    assert response.status_code == 400


def test_a_missing_entry_is_a_404(client: TestClient) -> None:
    start_season(client, member_names=["Alice"])

    assert client.post("/waitlist/99999/promote", json={}).status_code == 404
