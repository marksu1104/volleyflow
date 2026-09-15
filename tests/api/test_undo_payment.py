"""A payment or refund marked by mistake can be undone, once, by the organizer."""

from typing import Any

from fastapi.testclient import TestClient

from tests.api.factories import (
    auth_headers,
    create_club,
    identify,
    join_club,
    start_season,
)


def _club_with_alice(client: TestClient) -> tuple[int, int, int]:
    club = create_club(client, name="帳務")
    season = start_season(
        client,
        club_id=club["id"],
        organizer_token=club["organizer_token"],
        member_names=["Alice"],
        game_dates=["2031-01-07"],
    )
    return club["id"], season["id"], season["member_ids"][0]


def _ledger(client: TestClient, club_id: int, player_id: int) -> dict[str, Any]:
    ledger: dict[str, Any] = client.get(
        f"/clubs/{club_id}/players/{player_id}/ledger"
    ).json()
    return ledger


def _pay(client: TestClient, club_id: int, player_id: int, amount: str) -> int:
    entry_id: int = client.post(
        f"/clubs/{club_id}/players/{player_id}/payments", json={"amount": amount}
    ).json()["id"]
    return entry_id


def test_undoing_a_payment_puts_the_balance_back_and_keeps_the_original(
    client: TestClient,
) -> None:
    club_id, _season_id, alice = _club_with_alice(client)
    before = _ledger(client, club_id, alice)["balance"]
    paid = _pay(client, club_id, alice, "100")

    undo = client.post(f"/ledger-entries/{paid}/reverse")

    assert undo.status_code == 200
    assert undo.json()["reverses_entry_id"] == paid
    after = _ledger(client, club_id, alice)
    assert after["balance"] == before
    payments = [e["id"] for e in after["entries"] if e["entry_type"] == "payment"]
    assert payments == [paid, undo.json()["id"]]


def test_a_refund_can_be_undone_too(client: TestClient) -> None:
    club_id, _season_id, alice = _club_with_alice(client)
    before = _ledger(client, club_id, alice)["balance"]
    refunded = _pay(client, club_id, alice, "-50")

    client.post(f"/ledger-entries/{refunded}/reverse")

    assert _ledger(client, club_id, alice)["balance"] == before


def test_a_payment_can_only_be_undone_once(client: TestClient) -> None:
    club_id, _season_id, alice = _club_with_alice(client)
    paid = _pay(client, club_id, alice, "100")
    client.post(f"/ledger-entries/{paid}/reverse")

    again = client.post(f"/ledger-entries/{paid}/reverse")

    assert again.status_code == 400


def test_only_payments_and_refunds_can_be_undone(client: TestClient) -> None:
    club_id, _season_id, alice = _club_with_alice(client)
    fee = next(
        e["id"]
        for e in _ledger(client, club_id, alice)["entries"]
        if e["entry_type"] == "season_fee_charged"
    )

    response = client.post(f"/ledger-entries/{fee}/reverse")

    assert response.status_code == 400


def test_undoing_an_undo_is_refused(client: TestClient) -> None:
    club_id, _season_id, alice = _club_with_alice(client)
    paid = _pay(client, club_id, alice, "100")
    undo = client.post(f"/ledger-entries/{paid}/reverse").json()

    response = client.post(f"/ledger-entries/{undo['id']}/reverse")

    assert response.status_code == 400


def test_a_member_cannot_undo_a_payment(client: TestClient) -> None:
    club_id, _season_id, alice = _club_with_alice(client)
    paid = _pay(client, club_id, alice, "100")
    member = auth_headers(identify(client, "Member")["token"])
    join_club(client, club_id, member)

    response = client.post(f"/ledger-entries/{paid}/reverse", headers=member)

    assert response.status_code == 403
