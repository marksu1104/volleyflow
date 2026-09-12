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
    game = client.get(f"/seasons/{season['id']}").json()["games"][0]
    assert [p["player_name"] for p in game["confirmed_drop_ins"]] == ["Dave"]
    # Carol didn't withdraw — the organizer chose somebody else — so she
    # goes back to waiting rather than being deleted.
    assert [w["player_name"] for w in game["waitlist_entries"]] == ["Carol"]


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


def _future(weeks: int) -> str:
    from datetime import date, timedelta

    return str(date.today() + timedelta(weeks=weeks))


def test_taking_a_member_off_the_roster_offers_their_place_to_the_queue(
    client: TestClient,
) -> None:
    """Asked for on 2026-09-12, after the removal fix turned up a game
    sitting at 2 on a court for 3 with somebody still queued for it.

    CLAUDE.md 2.3 lists an absence and a cancelled signup as the moments
    a slot is offered on; a removal frees one just as squarely.
    """
    season = start_season(
        client,
        member_names=["Alice", "Bob", "Carol"],
        capacity=3,
        game_dates=[_future(1)],
    )
    game_id = season["games"][0]["id"]
    client.post("/drop-ins", json={"player_name": "訪客", "game_id": game_id})
    alice = client.get(f"/seasons/{season['id']}").json()["members"][0]

    client.delete(f"/seasons/{season['id']}/members/{alice['id']}")

    detail = client.get(f"/seasons/{season['id']}").json()
    game = detail["games"][0]
    assert [d["player_name"] for d in game["confirmed_drop_ins"]] == ["訪客"]
    assert game["waitlist_entries"] == []
    assert [m["name"] for m in detail["members"]] == ["Bob", "Carol"], (
        "and they came off the queue as a drop-in for that game, "
        "not into the fixed roster the leaver vacated"
    )


def test_the_promoted_person_is_charged_for_that_game(client: TestClient) -> None:
    season = start_season(
        client,
        total_venue_cost="900",
        member_names=["Alice", "Bob", "Carol"],
        capacity=3,
        game_dates=[_future(1)],
    )
    game_id = season["games"][0]["id"]
    guest = client.post(
        "/drop-ins", json={"player_name": "訪客", "game_id": game_id}
    ).json()
    alice = client.get(f"/seasons/{season['id']}").json()["members"][0]

    client.delete(f"/seasons/{season['id']}/members/{alice['id']}")

    ledger = client.get(
        f"/clubs/{season['club_id']}/players/{guest['player_id']}/ledger"
    ).json()
    assert ledger["balance"] == "-300", "one game's share, same as a direct signup"


def test_a_game_the_leaver_was_away_from_promotes_nobody(client: TestClient) -> None:
    # They were not on that court, so their removal frees nothing there.
    # Their stand-in already holds the place.
    season = start_season(
        client,
        member_names=["Alice", "Bob", "Carol"],
        capacity=3,
        game_dates=[_future(1)],
    )
    game_id = season["games"][0]["id"]
    client.post("/absences", json={"player_name": "Alice", "game_id": game_id})
    client.post("/drop-ins", json={"player_name": "代打", "game_id": game_id})
    client.post("/drop-ins", json={"player_name": "排隊的", "game_id": game_id})
    alice = client.get(f"/seasons/{season['id']}").json()["members"][0]

    client.delete(f"/seasons/{season['id']}/members/{alice['id']}")

    game = client.get(f"/seasons/{season['id']}").json()["games"][0]
    assert [w["player_name"] for w in game["waitlist_entries"]] == ["排隊的"]


def test_nobody_is_promoted_into_a_game_that_has_already_been_played(
    client: TestClient,
) -> None:
    # A removal frees the slot at every game in the season, last month's
    # included. Promoting somebody there would put them on a roster they
    # never stood on and charge them for the night.
    season = start_season(
        client,
        member_names=["Alice", "Bob", "Carol"],
        capacity=3,
        game_dates=["2026-01-06", _future(1)],
    )
    past_game = season["games"][0]["id"]
    client.post("/drop-ins", json={"player_name": "訪客", "game_id": past_game})
    alice = client.get(f"/seasons/{season['id']}").json()["members"][0]

    client.delete(f"/seasons/{season['id']}/members/{alice['id']}")

    past = client.get(f"/seasons/{season['id']}").json()["games"][0]
    assert [w["player_name"] for w in past["waitlist_entries"]] == ["訪客"]
    assert past["confirmed_drop_ins"] == []


def test_the_queue_never_fills_more_places_than_were_freed(
    client: TestClient,
) -> None:
    season = start_season(
        client,
        member_names=["Alice", "Bob", "Carol"],
        capacity=3,
        game_dates=[_future(1)],
    )
    game_id = season["games"][0]["id"]
    for name in ["甲", "乙", "丙"]:
        client.post("/drop-ins", json={"player_name": name, "game_id": game_id})
    alice = client.get(f"/seasons/{season['id']}").json()["members"][0]

    client.delete(f"/seasons/{season['id']}/members/{alice['id']}")

    detail = client.get(f"/seasons/{season['id']}").json()
    game = detail["games"][0]
    on_court = (
        len(detail["members"]) - len(game["absences"]) + len(game["confirmed_drop_ins"])
    )
    assert on_court == 3, f"one place freed, one filled: {on_court} on a court for 3"
    assert [d["player_name"] for d in game["confirmed_drop_ins"]] == ["甲"]
    assert [w["player_name"] for w in game["waitlist_entries"]] == ["乙", "丙"]
