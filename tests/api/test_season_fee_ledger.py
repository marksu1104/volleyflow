"""Tests for charging the season fee up front (at season creation / roster
or cost changes) instead of at season end — see docs/billing-rules.md
"When the season fee is charged" and "Keeping the charge in sync when the
inputs change".
"""

from fastapi.testclient import TestClient

from tests.api.factories import start_season


def test_starting_a_season_charges_each_members_season_fee(
    client: TestClient,
) -> None:
    season = start_season(
        client,
        total_venue_cost="10000",
        game_dates=["2026-08-18", "2026-08-25"],
        member_names=["Alice", "Bob"],
    )
    alice_id = season["member_ids"][0]

    ledger = client.get(f"/clubs/{season['club_id']}/players/{alice_id}/ledger").json()

    # share = ceil(10000 / 2 games / 2 members) = 2500; fee = 2500 * 2 = 5000
    assert ledger["balance"] == "-5000"
    assert len(ledger["entries"]) == 1
    assert ledger["entries"][0]["entry_type"] == "season_fee_charged"


def test_adding_a_member_charges_them_and_lowers_existing_shares(
    client: TestClient,
) -> None:
    season = start_season(
        client,
        total_venue_cost="10000",
        game_dates=["2026-08-18", "2026-08-25"],
        member_names=["Alice"],
    )
    alice_id = season["member_ids"][0]
    # before: share = ceil(10000/2/1) = 5000, fee = 10000

    response = client.post(
        f"/seasons/{season['id']}/members", json={"player_name": "Bob"}
    )
    bob_id = response.json()["id"]

    # after: 2 members, share = ceil(10000/2/2) = 2500, fee = 5000 each
    alice_ledger = client.get(
        f"/clubs/{season['club_id']}/players/{alice_id}/ledger"
    ).json()
    bob_ledger = client.get(
        f"/clubs/{season['club_id']}/players/{bob_id}/ledger"
    ).json()

    assert alice_ledger["balance"] == "-5000"
    assert len(alice_ledger["entries"]) == 2  # initial charge + adjustment
    assert bob_ledger["balance"] == "-5000"
    assert len(bob_ledger["entries"]) == 1


def test_removing_a_member_reverses_their_charge_and_raises_remaining_shares(
    client: TestClient,
) -> None:
    season = start_season(
        client,
        total_venue_cost="10000",
        game_dates=["2026-08-18", "2026-08-25"],
        member_names=["Alice", "Bob"],
    )
    alice_id, bob_id = season["member_ids"]
    # before: share = 2500, fee = 5000 each

    client.delete(f"/seasons/{season['id']}/members/{bob_id}")

    alice_ledger = client.get(
        f"/clubs/{season['club_id']}/players/{alice_id}/ledger"
    ).json()
    bob_ledger = client.get(
        f"/clubs/{season['club_id']}/players/{bob_id}/ledger"
    ).json()

    # after: 1 member, share = ceil(10000/2/1) = 5000, fee = 10000
    assert alice_ledger["balance"] == "-10000"
    assert len(alice_ledger["entries"]) == 2
    assert bob_ledger["balance"] == "0"
    assert len(bob_ledger["entries"]) == 2  # original charge + full reversal


def test_changing_venue_cost_adjusts_every_current_members_charge(
    client: TestClient,
) -> None:
    season = start_season(
        client,
        total_venue_cost="10000",
        game_dates=["2026-08-18", "2026-08-25"],
        member_names=["Alice"],
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
