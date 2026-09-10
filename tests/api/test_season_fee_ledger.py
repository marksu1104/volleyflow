"""Tests for charging the season fee up front (at season creation / roster
or cost changes) instead of at season end — see docs/billing-rules.md
"When the season fee is charged" and "Keeping the charge in sync when the
inputs change".
"""

from decimal import Decimal

from fastapi.testclient import TestClient

from tests.api.factories import auth_headers, identify, start_season


def test_starting_a_season_charges_each_members_season_fee(
    client: TestClient,
) -> None:
    season = start_season(
        client,
        total_venue_cost="10000",
        game_dates=["2026-08-18", "2026-08-25"],
        member_names=["Alice", "Bob"],
        capacity=2,
    )
    alice_id = season["member_ids"][0]

    ledger = client.get(f"/clubs/{season['club_id']}/players/{alice_id}/ledger").json()

    # share = ceil(10000 / 2 games / capacity 2) = 2500; fee = 2500 * 2 = 5000
    assert ledger["balance"] == "-5000"
    assert len(ledger["entries"]) == 1
    assert ledger["entries"][0]["entry_type"] == "season_fee_charged"


def test_adding_a_member_charges_them_and_leaves_everyone_else_alone(
    client: TestClient,
) -> None:
    # The rule changed on 2026-09-10: the venue cost is divided by the
    # season's capacity, not by however many are on the roster today. So
    # a new member costs the club nothing to add and nobody else's bill
    # moves. Before, one person joining or leaving silently re-priced
    # every other member's whole season.
    season = start_season(
        client,
        total_venue_cost="10000",
        game_dates=["2026-08-18", "2026-08-25"],
        member_names=["Alice"],
        capacity=2,
    )
    alice_id = season["member_ids"][0]
    # share = ceil(10000/2 games/capacity 2) = 2500, so Alice owes 5000

    response = client.post(
        f"/seasons/{season['id']}/members", json={"player_name": "Bob"}
    )
    bob_id = response.json()["id"]

    alice_ledger = client.get(
        f"/clubs/{season['club_id']}/players/{alice_id}/ledger"
    ).json()
    bob_ledger = client.get(
        f"/clubs/{season['club_id']}/players/{bob_id}/ledger"
    ).json()

    assert alice_ledger["balance"] == "-5000"
    assert len(alice_ledger["entries"]) == 1, "no adjustment: her price didn't move"
    assert bob_ledger["balance"] == "-5000"
    assert len(bob_ledger["entries"]) == 1


def test_removing_a_member_reverses_their_charge_and_leaves_everyone_else_alone(
    client: TestClient,
) -> None:
    season = start_season(
        client,
        total_venue_cost="10000",
        game_dates=["2026-08-18", "2026-08-25"],
        member_names=["Alice", "Bob"],
        capacity=2,
    )
    alice_id, bob_id = season["member_ids"]

    client.delete(f"/seasons/{season['id']}/members/{bob_id}")

    alice_ledger = client.get(
        f"/clubs/{season['club_id']}/players/{alice_id}/ledger"
    ).json()
    bob_ledger = client.get(
        f"/clubs/{season['club_id']}/players/{bob_id}/ledger"
    ).json()

    assert alice_ledger["balance"] == "-5000", "unchanged — she is not paying his share"
    assert len(alice_ledger["entries"]) == 1
    assert bob_ledger["balance"] == "0"
    assert len(bob_ledger["entries"]) == 2  # original charge + full reversal


def test_a_drop_in_filling_an_empty_slot_leaves_the_members_price_alone(
    client: TestClient,
) -> None:
    # Reported from real use: a roster short of capacity had every
    # member's night jump from $205 to $218 — while a drop-in stood in
    # the empty slot and paid the same $205. The club was collecting the
    # gap twice.
    season = start_season(
        client,
        total_venue_cost="10000",
        game_dates=["2026-08-18", "2026-08-25"],
        member_names=["Alice"],
        capacity=2,
    )
    alice_id = season["member_ids"][0]
    before = client.get(f"/clubs/{season['club_id']}/players/{alice_id}/ledger").json()

    client.post(
        "/drop-ins", json={"player_name": "Carol", "game_id": season["games"][0]["id"]}
    )

    after = client.get(f"/clubs/{season['club_id']}/players/{alice_id}/ledger").json()
    assert after == before, "somebody else turning up is not Alice's business"
    carol = client.get(f"/seasons/{season['id']}").json()["games"][0][
        "confirmed_drop_ins"
    ][0]
    assert carol["player_name"] == "Carol"


def test_changing_venue_cost_adjusts_every_current_members_charge(
    client: TestClient,
) -> None:
    season = start_season(
        client,
        total_venue_cost="10000",
        game_dates=["2026-08-18", "2026-08-25"],
        member_names=["Alice"],
        capacity=1,
    )
    alice_id = season["member_ids"][0]

    response = client.patch(
        f"/seasons/{season['id']}", json={"total_venue_cost": "20000"}
    )

    assert response.status_code == 200
    ledger = client.get(f"/clubs/{season['club_id']}/players/{alice_id}/ledger").json()
    # share = ceil(20000/2/1) = 10000, fee = 20000
    assert ledger["balance"] == "-20000"
    assert len(ledger["entries"]) == 2


def test_a_no_op_update_writes_no_adjustment_entry(client: TestClient) -> None:
    season = start_season(client, member_names=["Alice"])
    alice_id = season["member_ids"][0]

    client.patch(f"/seasons/{season['id']}", json={"location": "New Venue"})

    ledger = client.get(f"/clubs/{season['club_id']}/players/{alice_id}/ledger").json()
    assert len(ledger["entries"]) == 1


def test_cancelling_a_game_with_refund_credits_every_member_one_share(
    client: TestClient,
) -> None:
    season = start_season(
        client,
        total_venue_cost="10000",
        game_dates=["2026-08-18", "2026-08-25"],
        member_names=["Alice"],
        capacity=1,
    )
    alice_id = season["member_ids"][0]
    game_id = season["games"][0]["id"]
    # before: share = 5000, fee = 10000

    response = client.post(f"/games/{game_id}/cancel", json={"refunded": True})

    assert response.status_code == 200
    assert response.json()["status"] == "cancelled_refunded"
    ledger = client.get(f"/clubs/{season['club_id']}/players/{alice_id}/ledger").json()
    # billable_games drops from 2 to 1: fee = 5000 * 1 = 5000
    assert ledger["balance"] == "-5000"
    assert len(ledger["entries"]) == 2


def test_cancelling_a_game_without_refund_changes_nothing(client: TestClient) -> None:
    season = start_season(
        client,
        total_venue_cost="10000",
        game_dates=["2026-08-18", "2026-08-25"],
        member_names=["Alice"],
        capacity=1,
    )
    alice_id = season["member_ids"][0]
    game_id = season["games"][0]["id"]

    response = client.post(f"/games/{game_id}/cancel", json={"refunded": False})

    assert response.status_code == 200
    assert response.json()["status"] == "cancelled_unrefunded"
    ledger = client.get(f"/clubs/{season['club_id']}/players/{alice_id}/ledger").json()
    assert ledger["balance"] == "-10000"
    assert len(ledger["entries"]) == 1


def test_cancel_game_rejects_an_already_cancelled_game(client: TestClient) -> None:
    season = start_season(client, member_names=["Alice"])
    game_id = season["games"][0]["id"]
    client.post(f"/games/{game_id}/cancel", json={"refunded": False})

    response = client.post(f"/games/{game_id}/cancel", json={"refunded": True})

    assert response.status_code == 400


def test_cancel_game_rejects_once_season_is_settled(client: TestClient) -> None:
    season = start_season(client, member_names=["Alice"])
    game_id = season["games"][0]["id"]
    client.post(f"/seasons/{season['id']}/settle")

    response = client.post(f"/games/{game_id}/cancel", json={"refunded": True})

    assert response.status_code == 400


def test_cancel_game_for_unknown_game_returns_404(client: TestClient) -> None:
    start_season(client, member_names=["Alice"])

    response = client.post("/games/999999/cancel", json={"refunded": True})

    assert response.status_code == 404


def test_settling_a_season_does_not_recharge_the_season_fee(
    client: TestClient,
) -> None:
    season = start_season(client, member_names=["Alice"])
    alice_id = season["member_ids"][0]

    client.post(f"/seasons/{season['id']}/settle")

    ledger = client.get(f"/clubs/{season['club_id']}/players/{alice_id}/ledger").json()
    entry_types = [e["entry_type"] for e in ledger["entries"]]
    assert entry_types.count("season_fee_charged") == 1


def test_a_fixed_member_cannot_also_sign_up_as_a_drop_in(client: TestClient) -> None:
    """They're expected at every game already — signing up would charge
    the per-game share a second time and count them twice."""
    season = start_season(client, member_names=["Alice"])
    game_id = season["games"][0]["id"]

    response = client.post(
        "/drop-ins", json={"player_name": "Alice", "game_id": game_id}
    )

    assert response.status_code == 400


def test_adding_a_member_cancels_and_refunds_their_drop_ins(
    client: TestClient,
) -> None:
    """The ordinary sequence for someone new: they join the club, tap the
    signup button before anyone explains it, and the organizer adds them
    to the roster afterwards. Their drop-in fee has to come back off,
    or the same game is charged to them twice.
    """
    season = start_season(
        client,
        total_venue_cost="10000",
        game_dates=["2026-08-18", "2026-08-25"],
        member_names=["Alice"],
        capacity=2,
    )
    signup = client.post(
        "/drop-ins", json={"player_name": "Bob", "game_id": season["games"][0]["id"]}
    )
    bob_id = signup.json()["player_id"]
    charged = client.get(f"/clubs/{season['club_id']}/players/{bob_id}/ledger").json()
    assert Decimal(charged["balance"]) < 0, "the drop-in fee should be on his ledger"

    client.post(f"/seasons/{season['id']}/members", json={"player_name": "Bob"})

    ledger = client.get(f"/clubs/{season['club_id']}/players/{bob_id}/ledger").json()
    types = [e["entry_type"] for e in ledger["entries"]]
    assert types.count("drop_in_fee_charged") == 2, "charged then reversed"
    assert (
        sum(
            Decimal(e["amount"])
            for e in ledger["entries"]
            if e["entry_type"] == "drop_in_fee_charged"
        )
        == 0
    )
    # Left owing exactly one season fee: 2 games, capacity 2, ceil(10000/2/2)=2500
    assert ledger["balance"] == "-5000"


def test_adding_a_member_drops_them_off_the_waitlist(client: TestClient) -> None:
    season = start_season(client, member_names=["Alice"], capacity=1)
    game_id = season["games"][0]["id"]
    client.post("/drop-ins", json={"player_name": "Bob", "game_id": game_id})

    client.post(f"/seasons/{season['id']}/members", json={"player_name": "Bob"})

    game = next(
        g
        for g in client.get(f"/seasons/{season['id']}").json()["games"]
        if g["id"] == game_id
    )
    assert game["waitlist_entries"] == []
    assert game["confirmed_drop_ins"] == []


def test_a_roster_marks_who_has_no_line_account(client: TestClient) -> None:
    """Alice was typed in by hand and can't act for herself; the
    organizer needs to see that on the roster."""
    season = start_season(client, member_names=["Alice"])

    members = client.get(f"/seasons/{season['id']}").json()["members"]

    alice = next(m for m in members if m["name"] == "Alice")
    assert alice["linked"] is False
    club_members = client.get(f"/clubs/{season['club_id']}/members").json()
    organizer = next(m for m in club_members if m["role"] == "organizer")
    assert organizer["linked"] is True


def test_a_member_who_logged_in_is_not_marked_a_guest(client: TestClient) -> None:
    """The negative case alone passed while every member was wrongly
    marked a guest — get_season built its roster without the field and
    the default filled in False. This is the case that fails then.
    """
    season = start_season(client, member_names=["Alice"])
    bob = identify(client, "Bob")
    client.post(f"/clubs/{season['club_id']}/join", headers=auth_headers(bob["token"]))
    client.post(f"/seasons/{season['id']}/members", json={"player_name": bob["name"]})

    members = client.get(f"/seasons/{season['id']}").json()["members"]

    assert next(m for m in members if m["name"] == bob["name"])["linked"] is True
    assert next(m for m in members if m["name"] == "Alice")["linked"] is False
