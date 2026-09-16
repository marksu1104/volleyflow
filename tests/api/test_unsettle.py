"""Undoing a settlement.

Settling credits every member's absence refund and locks the season.
Undoing it cancels each of those with an opposite entry and unlocks the
season — without deleting anything, because the ledger is append-only.
See routes/seasons.py unsettle_season.
"""

from typing import Any

from fastapi.testclient import TestClient

from tests.api.factories import auth_headers, identify, join_club, start_season


def _season_with_a_refund(client: TestClient) -> tuple[dict[str, Any], int]:
    """Alice takes the night off and a drop-in fills her place, which is
    the only way an absence is refunded at all."""
    season = start_season(
        client,
        game_dates=["2031-05-06", "2031-05-13"],
        member_names=["Alice", "Bob"],
        capacity=2,
    )
    game_id = season["games"][0]["id"]
    client.post("/absences", json={"player_name": "Alice", "game_id": game_id})
    client.post("/drop-ins", json={"player_name": "Carol", "game_id": game_id})

    members = client.get(f"/seasons/{season['id']}").json()["members"]
    alice = next(m["id"] for m in members if m["name"] == "Alice")
    return season, alice


def _ledger(
    client: TestClient, season: dict[str, Any], player_id: int
) -> dict[str, Any]:
    body: dict[str, Any] = client.get(
        f"/clubs/{season['club_id']}/players/{player_id}/ledger"
    ).json()
    return body


def test_undoing_a_settlement_puts_the_balance_back(client: TestClient) -> None:
    season, alice = _season_with_a_refund(client)
    before = _ledger(client, season, alice)["balance"]
    client.post(f"/seasons/{season['id']}/settle")
    settled = _ledger(client, season, alice)["balance"]
    assert settled != before, "the refund should have moved her balance"

    undone = client.post(f"/seasons/{season['id']}/unsettle")

    assert undone.status_code == 200, undone.text
    assert undone.json()["reversed_entries"] == 1
    assert _ledger(client, season, alice)["balance"] == before
    assert client.get(f"/seasons/{season['id']}").json()["settled_at"] is None


def test_the_original_refund_stays_on_the_books(client: TestClient) -> None:
    # Append-only: the history still says a settlement happened and was
    # undone, rather than pretending it never did.
    season, alice = _season_with_a_refund(client)
    client.post(f"/seasons/{season['id']}/settle")
    after_settling = _ledger(client, season, alice)["entries"]

    client.post(f"/seasons/{season['id']}/unsettle")

    entries = _ledger(client, season, alice)["entries"]
    assert len(entries) == len(after_settling) + 1
    refunds = [e for e in entries if e["entry_type"] == "absence_refund"]
    assert len(refunds) == 2, "the original and its reversal"
    assert sum(float(e["amount"]) for e in refunds) == 0


def test_a_season_that_is_not_settled_cannot_be_undone(client: TestClient) -> None:
    season, _ = _season_with_a_refund(client)

    refused = client.post(f"/seasons/{season['id']}/unsettle")

    assert refused.status_code == 400
    assert "not settled" in refused.json()["detail"]


def test_undoing_twice_is_refused_rather_than_doubling_up(client: TestClient) -> None:
    season, alice = _season_with_a_refund(client)
    client.post(f"/seasons/{season['id']}/settle")
    client.post(f"/seasons/{season['id']}/unsettle")
    balance = _ledger(client, season, alice)["balance"]

    again = client.post(f"/seasons/{season['id']}/unsettle")

    assert again.status_code == 400
    assert _ledger(client, season, alice)["balance"] == balance


def test_cash_already_handed_over_is_named(client: TestClient) -> None:
    # The one thing undoing cannot put right. Her refund left the
    # organizer's hand, so it now reads as owed back — and she is named
    # rather than counted, because somebody has to talk to her.
    season, alice = _season_with_a_refund(client)
    settled = client.post(f"/seasons/{season['id']}/settle").json()
    # Her refund, not her balance: the season fee is charged on joining
    # the roster and is the larger of the two, so the balance after
    # settling is still what she owes.
    refund = next(m["refund"] for m in settled["members"] if m["player_id"] == alice)
    assert float(refund) > 0, "the drop-in covered her absence, so she is owed one game"
    client.post(
        f"/clubs/{season['club_id']}/players/{alice}/payments",
        json={"amount": f"-{refund}", "season_id": season["id"], "note": "退現金"},
    )

    undone = client.post(f"/seasons/{season['id']}/unsettle")

    assert undone.status_code == 200, undone.text
    named = undone.json()["already_paid_out"]
    assert [p["player_name"] for p in named] == ["Alice"]
    assert float(named[0]["amount"]) == float(refund)


def test_settling_again_after_undoing_lands_in_the_same_place(
    client: TestClient,
) -> None:
    season, alice = _season_with_a_refund(client)
    client.post(f"/seasons/{season['id']}/settle")
    once = _ledger(client, season, alice)["balance"]

    client.post(f"/seasons/{season['id']}/unsettle")
    client.post(f"/seasons/{season['id']}/settle")

    assert _ledger(client, season, alice)["balance"] == once


def test_a_member_may_not_undo_a_settlement(client: TestClient) -> None:
    season, _ = _season_with_a_refund(client)
    client.post(f"/seasons/{season['id']}/settle")
    member = identify(client, "Mallory")
    join_club(client, season["club_id"], auth_headers(member["token"]))

    refused = client.post(
        f"/seasons/{season['id']}/unsettle", headers=auth_headers(member["token"])
    )

    assert refused.status_code == 403
