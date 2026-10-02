"""The money flow from one season into the next, end to end.

Each step has tests of its own; this one checks they add up. The rule it
holds the whole flow to: once the first season is settled and the second
season's fees are collected, every member's balance up to the second
season is exactly $0. Before 2026-10-02 it wasn't — whatever a member
kept for next season sat on the books untouched while the 季費 tab
collected the plain fee, and a season booked further ahead leaked its fee
into the settlement. A third season is booked here for exactly that.
"""

from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from fastapi.testclient import TestClient

from tests.api.factories import start_season

MEMBERS = ["Alice", "Bob", "Carol"]


def _in(days: int) -> str:
    return (date.today() + timedelta(days=days)).isoformat()


def _rows(client: TestClient, season: dict[str, Any]) -> dict[int, dict[str, Any]]:
    rows = client.get(
        f"/clubs/{season['club_id']}/balances", params={"season_id": season["id"]}
    ).json()
    return {r["player_id"]: r for r in rows}


def _due(client: TestClient, season: dict[str, Any], player_id: int) -> Decimal:
    return Decimal(_rows(client, season)[player_id]["through_season"])


def test_two_seasons_square_to_zero_with_a_third_already_booked(
    client: TestClient,
) -> None:
    autumn = start_season(
        client,
        member_names=MEMBERS,
        capacity=3,
        game_dates=["2026-07-07", "2026-07-14"],
    )
    club = autumn["club_id"]
    winter = start_season(
        client,
        member_names=MEMBERS,
        capacity=3,
        game_dates=[_in(14), _in(21)],
        club_id=club,
    )
    spring = start_season(
        client, member_names=MEMBERS, capacity=3, game_dates=[_in(120)], club_id=club
    )
    alice, bob, carol = autumn["member_ids"]

    # Autumn: Alice misses a night and a drop-in fills it, so she is owed
    # a refund. Bob paid 700 short. Carol paid exactly.
    first_night = autumn["games"][0]["id"]
    client.post("/absences", json={"player_name": "Alice", "game_id": first_night})
    client.post("/drop-ins", json={"player_name": "Dave", "game_id": first_night})
    autumn_fee = -Decimal(_rows(client, autumn)[alice]["season_fee_charged"])
    for player, paid in [
        (alice, autumn_fee),
        (bob, autumn_fee - 700),
        (carol, autumn_fee),
    ]:
        client.post(
            f"/clubs/{club}/players/{player}/payments",
            json={"amount": str(paid), "season_id": autumn["id"]},
        )

    # Autumn's figures leave winter and spring out entirely.
    assert _due(client, autumn, bob) == -700
    assert _due(client, autumn, carol) == 0

    # Settled with nobody squared in cash: everything is kept for winter.
    settled = client.post(f"/seasons/{autumn['id']}/settle", json={})
    assert settled.status_code == 200, settled.text
    refund = next(
        Decimal(m["refund"])
        for m in settled.json()["members"]
        if m["player_id"] == alice
    )
    assert refund > 0
    assert _due(client, autumn, alice) == refund

    # Spring is months away and cannot be settled by mistake.
    early = client.post(f"/seasons/{spring['id']}/settle", json={})
    assert early.status_code == 400

    # Winter's 季費: last season's balance comes off or goes on.
    winter_fee = -Decimal(_rows(client, winter)[alice]["season_fee_charged"])
    assert _due(client, winter, alice) == -(winter_fee - refund)
    assert _due(client, winter, bob) == -(winter_fee + 700)
    assert _due(client, winter, carol) == -winter_fee

    # 已收 on each row records exactly what the row says.
    for player in (alice, bob, carol):
        owed = -_due(client, winter, player)
        client.post(
            f"/clubs/{club}/players/{player}/payments",
            json={"amount": str(owed), "season_id": winter["id"]},
        )

    for player in (alice, bob, carol):
        assert _due(client, winter, player) == 0, "nobody owes anything up to winter"
        assert _due(client, spring, player) == Decimal(
            _rows(client, spring)[player]["season_fee_charged"]
        ), "spring's fee is still to be collected, and only spring's"
