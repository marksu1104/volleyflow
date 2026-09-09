"""Adding somebody to the fixed roster and taking them off again.

Reported from real use: "I added a member to the season roster and then
removed them, and their past drop-in record had disappeared." Written to
find out whether that is true before deciding what to do about it.
"""

from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.api.factories import start_season


def _ledger_total(client: TestClient, season: dict[str, Any], player_id: int) -> float:
    body = client.get(f"/clubs/{season['club_id']}/players/{player_id}/ledger").json()
    return float(body["balance"])


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Confirmed bug, 2026-09-10, not yet fixed. Adding somebody to the "
        "roster cancels their drop-ins for the season and refunds the fees "
        "(_absorb_drop_ins_into_membership) so they are not charged twice "
        "for the same night. Removing them from the roster reverses the "
        "season fee but never puts those drop-ins back, so a night they "
        "actually played disappears and the club is short that fee. "
        "strict=True: when this starts passing, delete the marker."
    ),
)
def test_a_drop_in_survives_being_made_a_member_and_unmade(
    client: TestClient,
) -> None:
    season = start_season(client, member_names=["Alice"], capacity=18)
    game = season["games"][0]
    carol = client.post(
        "/drop-ins", json={"player_name": "Carol", "game_id": game["id"]}
    ).json()
    assert carol["status"] == "confirmed"
    owed_as_drop_in = _ledger_total(client, season, carol["player_id"])
    assert owed_as_drop_in != 0, "she played one game and was charged for it"

    client.post(f"/seasons/{season['id']}/members", json={"player_name": "Carol"})
    client.delete(f"/seasons/{season['id']}/members/{carol['player_id']}")

    playing = client.get(f"/seasons/{season['id']}").json()["games"][0][
        "confirmed_drop_ins"
    ]
    assert [p["player_name"] for p in playing] == ["Carol"], (
        "she was on court that night; taking her off the roster cannot "
        "erase that she played"
    )
    assert _ledger_total(client, season, carol["player_id"]) == owed_as_drop_in, (
        "and she still owes for the game she played"
    )
