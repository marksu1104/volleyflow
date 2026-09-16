"""Moving one game to another date.

The venue shifts a booking, or the club swaps an evening. Everything
already recorded against that night has to come with it, and nobody's
money may move — see routes.games.move_game.
"""

from datetime import date, timedelta
from typing import Any

from fastapi.testclient import TestClient

from tests.api.factories import auth_headers, identify, join_club, start_season


def _in(days: int) -> str:
    """A date far enough ahead that the server's Taiwan "today" and the
    test runner's clock cannot disagree about it."""
    return (date.today() + timedelta(days=days)).isoformat()


def _season(client: TestClient) -> dict[str, Any]:
    return start_season(client, game_dates=[_in(30), _in(37)], member_names=["Alice"])


def test_moving_a_game_takes_everything_recorded_against_it_along(
    client: TestClient,
) -> None:
    season = _season(client)
    game = season["games"][0]
    client.post("/absences", json={"player_name": "Alice", "game_id": game["id"]})
    client.post("/drop-ins", json={"player_name": "Carol", "game_id": game["id"]})

    moved = client.patch(f"/games/{game['id']}", json={"date": _in(31)})

    assert moved.status_code == 200, moved.text
    assert moved.json()["date"] == _in(31)
    after = client.get(f"/seasons/{season['id']}").json()["games"][0]
    assert after["date"] == _in(31)
    assert [a["player_name"] for a in after["absences"]] == ["Alice"]
    assert [d["player_name"] for d in after["confirmed_drop_ins"]] == ["Carol"]


def test_moving_a_game_charges_nobody_anything(client: TestClient) -> None:
    # The season keeps the same number of games at the same share, so the
    # ledger has no reason to move — and if it ever does, it will be this
    # that catches it.
    season = start_season(
        client, game_dates=[_in(30), _in(37)], member_names=["Alice"], capacity=2
    )
    ledger_before = client.get(
        f"/clubs/{season['club_id']}/players/{season['member_ids'][0]}/ledger"
    ).json()

    client.patch(f"/games/{season['games'][0]['id']}", json={"date": _in(31)})

    ledger_after = client.get(
        f"/clubs/{season['club_id']}/players/{season['member_ids'][0]}/ledger"
    ).json()
    assert ledger_after["balance"] == ledger_before["balance"]
    assert len(ledger_after["entries"]) == len(ledger_before["entries"])


def test_two_games_may_not_share_one_evening(client: TestClient) -> None:
    season = _season(client)

    refused = client.patch(
        f"/games/{season['games'][0]['id']}", json={"date": season["games"][1]["date"]}
    )

    assert refused.status_code == 400
    assert "already has a game" in refused.json()["detail"]


def test_a_game_cannot_be_moved_into_the_past(client: TestClient) -> None:
    season = _season(client)

    refused = client.patch(f"/games/{season['games'][0]['id']}", json={"date": _in(-3)})

    assert refused.status_code == 400
    assert "been and gone" in refused.json()["detail"]


def test_a_cancelled_game_cannot_be_moved(client: TestClient) -> None:
    # A called-off night is a statement about a date. Moving it would
    # rewrite what happened rather than schedule anything.
    season = _season(client)
    game_id = season["games"][0]["id"]
    client.post(f"/games/{game_id}/cancel", json={"refunded": True})

    refused = client.patch(f"/games/{game_id}", json={"date": _in(31)})

    assert refused.status_code == 400
    assert "cancelled" in refused.json()["detail"]


def test_a_settled_season_refuses_a_move(client: TestClient) -> None:
    season = start_season(client, game_dates=[_in(30)], member_names=["Alice"])
    client.post(f"/seasons/{season['id']}/settle")

    refused = client.patch(f"/games/{season['games'][0]['id']}", json={"date": _in(31)})

    assert refused.status_code == 400
    assert "settled" in refused.json()["detail"]


def test_a_member_may_not_move_a_game(client: TestClient) -> None:
    season = _season(client)
    member = identify(client, "Mallory")
    join_club(client, season["club_id"], auth_headers(member["token"]))

    refused = client.patch(
        f"/games/{season['games'][0]['id']}",
        json={"date": _in(31)},
        headers=auth_headers(member["token"]),
    )

    assert refused.status_code == 403
