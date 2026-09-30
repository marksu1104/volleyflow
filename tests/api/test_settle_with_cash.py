"""Squaring up in cash while settling, rather than afterwards.

Settling used to write the absence refunds and stop there; collecting or
handing back the difference was a second errand down the member list.
The organizer asked for one pass: decide per person, then settle.

The case that matters most here is the one that writes nothing — a
balance left alone is what carries into the next season, and 「保留至下一
季」 is precisely the absence of a payment.
"""

from decimal import Decimal
from typing import Any

from fastapi.testclient import TestClient

from tests.api.factories import auth_headers, create_club, identify, start_season


def _season_with_a_refund(client: TestClient) -> dict[str, Any]:
    """Alice is owed one game's share: she took the night off and a
    drop-in filled the slot."""
    season = start_season(
        client,
        member_names=["Alice", "Bob"],
        capacity=2,
        game_dates=["2026-08-18"],
    )
    game_id = season["games"][0]["id"]
    client.post("/absences", json={"player_name": "Alice", "game_id": game_id})
    client.post("/drop-ins", json={"player_name": "訪客", "game_id": game_id})
    members = client.get(f"/seasons/{season['id']}").json()["members"]
    season["alice_id"] = next(m["id"] for m in members if m["name"] == "Alice")
    season["bob_id"] = next(m["id"] for m in members if m["name"] == "Bob")
    return season


def _balance(client: TestClient, club_id: int, player_id: int) -> Decimal:
    rows = client.get(f"/clubs/{club_id}/balances").json()
    row = next(r for r in rows if r["player_id"] == player_id)
    return Decimal(row["balance"])


def test_settling_without_cash_leaves_every_balance_standing(
    client: TestClient,
) -> None:
    # 保留至下一季, which is the default and writes nothing at all.
    season = _season_with_a_refund(client)
    before = _balance(client, season["club_id"], season["bob_id"])

    response = client.post(f"/seasons/{season['id']}/settle", json={})

    assert response.status_code == 200, response.text
    assert _balance(client, season["club_id"], season["bob_id"]) == before, (
        "nothing handed over, so nothing moves — the balance carries"
    )


def test_cash_handed_over_is_recorded_in_the_same_pass(client: TestClient) -> None:
    season = _season_with_a_refund(client)
    owed_by_bob = -_balance(client, season["club_id"], season["bob_id"])
    assert owed_by_bob > 0, "the setup must leave Bob owing something"

    response = client.post(
        f"/seasons/{season['id']}/settle",
        json={"cash": [{"player_id": season["bob_id"], "amount": str(owed_by_bob)}]},
    )

    assert response.status_code == 200, response.text
    assert _balance(client, season["club_id"], season["bob_id"]) == 0


def test_a_refund_paid_out_settles_against_the_refund_just_credited(
    client: TestClient,
) -> None:
    """Order matters: the payment is written after the absence refund, so
    handing back exactly what the settlement credited leaves zero."""
    season = _season_with_a_refund(client)
    club_id, alice = season["club_id"], season["alice_id"]

    # She paid her season fee up front, as members do, so the refund the
    # settlement credits is money the club then owes her.
    owed = -_balance(client, club_id, alice)
    client.post(
        f"/clubs/{club_id}/players/{alice}/payments", json={"amount": str(owed)}
    )
    assert _balance(client, club_id, alice) == 0

    preview = client.get(f"/seasons/{season['id']}/settlement").json()
    refund = Decimal(
        next(m for m in preview["members"] if m["player_id"] == alice)["refund"]
    )
    assert refund > 0, "the setup must leave Alice something to be paid back"

    client.post(
        f"/seasons/{season['id']}/settle",
        json={"cash": [{"player_id": alice, "amount": str(-refund)}]},
    )

    assert _balance(client, club_id, alice) == 0


def test_somebody_outside_the_season_is_refused(client: TestClient) -> None:
    # Writing money against the wrong id is worse than refusing.
    season = _season_with_a_refund(client)
    stranger = identify(client, "Stranger")

    response = client.post(
        f"/seasons/{season['id']}/settle",
        json={"cash": [{"player_id": stranger["id"], "amount": "100"}]},
    )

    assert response.status_code == 400
    assert str(stranger["id"]) in response.json()["detail"]
    assert client.get(f"/seasons/{season['id']}").json()["settled_at"] is None, (
        "a refused settlement must not have locked the season"
    )


def test_only_the_organizer_may_settle_with_cash(client: TestClient) -> None:
    season = _season_with_a_refund(client)
    outsider = identify(client, "Outsider")
    create_club(client, name="Another")

    response = client.post(
        f"/seasons/{season['id']}/settle",
        json={"cash": []},
        headers=auth_headers(outsider["token"]),
    )

    assert response.status_code == 403
