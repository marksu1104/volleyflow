"""Tests for drop-in fee charging, season settlement, and the ledger."""

from fastapi.testclient import TestClient

from tests.api.factories import auth_headers, create_club, identify, start_season


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
    # Carol is never added to a season, so nothing has ever charged or
    # credited her — unlike a season member, who is charged their season
    # fee the moment the season starts (see test_season_fee_ledger.py).
    club = create_club(client)
    carol = identify(client, "Carol")

    ledger = client.get(f"/clubs/{club['id']}/players/{carol['id']}/ledger").json()

    assert ledger["balance"] == "0"
    assert ledger["entries"] == []


# --- repeating a payment ---------------------------------------------------
#
# A tap that times out on a phone, a double tap, a retry: all of them can
# put the same request on the wire twice. Money must land once.


def test_the_same_payment_token_records_one_entry(client: TestClient) -> None:
    season = start_season(client, member_names=["Alice"])
    alice_id = season["member_ids"][0]
    body = {"amount": "500", "season_id": season["id"], "client_token": "tap-1"}

    first = client.post(
        f"/clubs/{season['club_id']}/players/{alice_id}/payments", json=body
    )
    second = client.post(
        f"/clubs/{season['club_id']}/players/{alice_id}/payments", json=body
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["id"] == first.json()["id"], "the same entry, not a new one"
    ledger = client.get(f"/clubs/{season['club_id']}/players/{alice_id}/ledger").json()
    payments = [e for e in ledger["entries"] if e["entry_type"] == "payment"]
    assert len(payments) == 1
    assert payments[0]["amount"] == "500"


def test_different_tokens_record_separate_payments(client: TestClient) -> None:
    """Two genuinely separate payments must still both land — the guard
    is against repeats, not against paying twice on purpose."""
    season = start_season(client, member_names=["Alice"])
    alice_id = season["member_ids"][0]

    for token in ("tap-1", "tap-2"):
        client.post(
            f"/clubs/{season['club_id']}/players/{alice_id}/payments",
            json={"amount": "500", "client_token": token},
        )

    ledger = client.get(f"/clubs/{season['club_id']}/players/{alice_id}/ledger").json()
    payments = [e for e in ledger["entries"] if e["entry_type"] == "payment"]
    assert len(payments) == 2


def test_a_payment_without_a_token_still_works(client: TestClient) -> None:
    """Older callers, and anything that genuinely wants two identical
    entries, are unaffected."""
    season = start_season(client, member_names=["Alice"])
    alice_id = season["member_ids"][0]

    for _ in range(2):
        response = client.post(
            f"/clubs/{season['club_id']}/players/{alice_id}/payments",
            json={"amount": "100"},
        )
        assert response.status_code == 200

    ledger = client.get(f"/clubs/{season['club_id']}/players/{alice_id}/ledger").json()
    assert len([e for e in ledger["entries"] if e["entry_type"] == "payment"]) == 2


# --- the whole club's balances in one request ------------------------------


def test_balances_sums_every_member_in_one_call(client: TestClient) -> None:
    season = start_season(
        client,
        total_venue_cost="10000",
        game_dates=["2026-08-18", "2026-08-25"],
        member_names=["Alice", "Bob"],
    )
    alice_id, bob_id = season["member_ids"]
    client.post(
        f"/clubs/{season['club_id']}/players/{alice_id}/payments",
        json={"amount": "5000", "season_id": season["id"]},
    )

    rows = client.get(
        f"/clubs/{season['club_id']}/balances?season_id={season['id']}"
    ).json()

    by_player = {r["player_id"]: r for r in rows}
    # share = ceil(10000/2 games/2 members) = 2500, fee = 5000 each
    assert by_player[alice_id]["season_fee_charged"] == "-5000"
    assert by_player[alice_id]["balance"] == "0", "paid in full"
    assert by_player[bob_id]["balance"] == "-5000", "not paid"


def test_balances_separates_this_season_from_the_rest(client: TestClient) -> None:
    """The screen shows 本季季費 and 上季餘額 side by side, so the split has
    to come back from the same call."""
    season = start_season(client, member_names=["Alice"])
    alice_id = season["member_ids"][0]
    client.post(
        f"/clubs/{season['club_id']}/players/{alice_id}/payments",
        json={"amount": "300"},  # no season_id: carried money, not this season's
    )

    row = next(
        r
        for r in client.get(
            f"/clubs/{season['club_id']}/balances?season_id={season['id']}"
        ).json()
        if r["player_id"] == alice_id
    )

    assert row["balance"] == str(int(row["season_total"]) + 300)
    assert row["season_total"] == row["season_fee_charged"]


def test_a_member_cannot_read_the_whole_clubs_balances(client: TestClient) -> None:
    season = start_season(client, member_names=["Alice"])
    carol = identify(client, "Carol")
    client.post(
        f"/clubs/{season['club_id']}/join", headers=auth_headers(carol["token"])
    )

    response = client.get(
        f"/clubs/{season['club_id']}/balances",
        headers=auth_headers(carol["token"]),
    )

    assert response.status_code == 403
