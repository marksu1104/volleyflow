"""Tests for drop-in fee charging, season settlement, and the ledger."""

from fastapi.testclient import TestClient

from tests.api.factories import create_club, start_season


def test_sign_up_charges_the_drop_in_fee(client: TestClient) -> None:
    season = start_season(client, member_names=["Alice"], capacity=18)
    game_id = season["games"][0]["id"]

    signup = client.post("/drop-ins", json={"player_name": "Carol", "game_id": game_id})
    player_id = signup.json()["player_id"]

    ledger = client.get(f"/clubs/{season['club_id']}/players/{player_id}/ledger").json()

    assert ledger["balance"] == "-5000"  # ceil(10000/2 games/1 member)=5000
    assert len(ledger["entries"]) == 1
    assert ledger["entries"][0]["entry_type"] == "drop_in_fee_charged"


def test_cancel_drop_in_refunds_the_fee(client: TestClient) -> None:
    season = start_season(client, member_names=["Alice"], capacity=18)
    game_id = season["games"][0]["id"]
    signup = client.post("/drop-ins", json={"player_name": "Carol", "game_id": game_id})
    player_id = signup.json()["player_id"]

    client.post(f"/drop-ins/{signup.json()['id']}/cancel")

    ledger = client.get(f"/clubs/{season['club_id']}/players/{player_id}/ledger").json()

    assert ledger["balance"] == "0"
    assert len(ledger["entries"]) == 2


def test_promoted_from_waitlist_gets_charged(client: TestClient) -> None:
    # 1 member + capacity 2 -> exactly one open drop-in slot.
    season = start_season(client, member_names=["Alice"], capacity=2)
    game_id = season["games"][0]["id"]
    confirmed = client.post(
        "/drop-ins", json={"player_name": "Bob", "game_id": game_id}
    )
    waitlisted = client.post(
        "/drop-ins", json={"player_name": "Carol", "game_id": game_id}
    )
    carol_id = waitlisted.json()["player_id"]

    client.post(f"/drop-ins/{confirmed.json()['id']}/cancel")

    ledger = client.get(f"/clubs/{season['club_id']}/players/{carol_id}/ledger").json()

    assert len(ledger["entries"]) == 1
    assert ledger["entries"][0]["entry_type"] == "drop_in_fee_charged"
    assert ledger["balance"] == "-5000"  # ceil(10000/2 games/1 member)=5000


def test_settle_season_charges_fees_and_credits_refunds(client: TestClient) -> None:
    season = start_season(
        client,
        total_venue_cost="10000",
        game_dates=[f"2026-08-{18 + i:02d}" for i in range(8)],
        member_names=["Alice", "Bob", "Carol", "Dave", "Eve"],
    )
    game_id = season["games"][0]["id"]
    client.post("/absences", json={"player_name": "Alice", "game_id": game_id})
    client.post("/drop-ins", json={"player_name": "Frank", "game_id": game_id})

    response = client.post(f"/seasons/{season['id']}/settle")

    assert response.status_code == 200
    body = response.json()
    assert body["settled_at"] is not None
    alice = next(m for m in body["members"] if m["player_name"] == "Alice")
    assert alice["net"] == "-1750"

    alice_id = season["member_ids"][0]
    ledger = client.get(f"/clubs/{season['club_id']}/players/{alice_id}/ledger").json()
    assert ledger["balance"] == "-1750"
    entry_types = {e["entry_type"] for e in ledger["entries"]}
    assert entry_types == {"season_fee_charged", "absence_refund"}


def test_settle_season_marks_the_season_settled(client: TestClient) -> None:
    season = start_season(client, member_names=["Alice"])

    client.post(f"/seasons/{season['id']}/settle")
    detail = client.get(f"/seasons/{season['id']}").json()

    assert detail["settled_at"] is not None


def test_settle_season_rejects_settling_twice(client: TestClient) -> None:
    season = start_season(client, member_names=["Alice"])
    client.post(f"/seasons/{season['id']}/settle")

    response = client.post(f"/seasons/{season['id']}/settle")

    assert response.status_code == 400


def test_settle_unknown_season_returns_404(client: TestClient) -> None:
    create_club(client)

    response = client.post("/seasons/999999/settle")

    assert response.status_code == 404


def test_record_payment_creates_a_ledger_entry(client: TestClient) -> None:
    season = start_season(client, member_names=["Alice"])
    alice_id = season["member_ids"][0]

    response = client.post(
        f"/clubs/{season['club_id']}/players/{alice_id}/payments",
        json={"amount": "2002", "season_id": season["id"], "note": "cash"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["entry_type"] == "payment"
    assert body["amount"] == "2002"
    assert body["note"] == "cash"


def test_payment_settles_a_charge_to_zero(client: TestClient) -> None:
    season = start_season(client, member_names=["Alice"])
    alice_id = season["member_ids"][0]
    client.post(f"/seasons/{season['id']}/settle")
    fee = client.get(f"/clubs/{season['club_id']}/players/{alice_id}/ledger").json()[
        "balance"
    ]

    client.post(
        f"/clubs/{season['club_id']}/players/{alice_id}/payments",
        json={"amount": str(-int(fee))},
    )

    ledger = client.get(f"/clubs/{season['club_id']}/players/{alice_id}/ledger").json()
    assert ledger["balance"] == "0"


def test_payment_for_an_unknown_player_returns_404(client: TestClient) -> None:
    club = create_club(client)

    response = client.post(
        f"/clubs/{club['id']}/players/999999/payments", json={"amount": "100"}
    )

    assert response.status_code == 404


def test_ledger_for_an_unknown_player_returns_404(client: TestClient) -> None:
    club = create_club(client)

    response = client.get(f"/clubs/{club['id']}/players/999999/ledger")

    assert response.status_code == 404


def test_ledger_balance_is_zero_with_no_entries(client: TestClient) -> None:
    season = start_season(client, member_names=["Alice"])
    alice_id = season["member_ids"][0]

    ledger = client.get(f"/clubs/{season['club_id']}/players/{alice_id}/ledger").json()

    assert ledger["balance"] == "0"
    assert ledger["entries"] == []
