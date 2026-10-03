"""Who signed a guest up stays true while the guest moves between lists.

Reported 2026-10-03 on production: a queued guest's 「X 報名」 tag was
right in the morning and named somebody else by the afternoon.
"""

from typing import Any

from fastapi.testclient import TestClient

from tests.api.factories import (
    auth_headers,
    create_club,
    identify,
    join_club,
    start_season,
)


def _queue(client: TestClient) -> tuple[dict[str, Any], int, dict[str, str]]:
    club = create_club(client, name="報名標籤")
    season = start_season(
        client,
        club_id=club["id"],
        organizer_token=club["organizer_token"],
        member_names=["固定甲", "固定乙"],
        capacity=2,
        game_dates=["2031-01-07"],
    )
    game_id = season["games"][0]["id"]
    bringer = auth_headers(identify(client, "會員丙")["token"])
    join_club(client, club["id"], bringer)
    queued = client.post(
        f"/games/{game_id}/drop-ins",
        json={"people": [{"player_name": "朋友丁", "gender": "male"}]},
        headers=bringer,
    ).json()["results"][0]
    assert queued["status"] == "waitlisted"
    return season, game_id, bringer


def _tags(client: TestClient, season: dict[str, Any]) -> dict[str, Any]:
    game = client.get(f"/seasons/{season['id']}").json()["games"][0]
    return {
        "queue": {
            w["player_name"]: w["brought_by_name"] for w in game["waitlist_entries"]
        },
        "court": {
            d["player_name"]: d["brought_by_name"] for d in game["confirmed_drop_ins"]
        },
    }


def test_a_queued_guest_named_as_a_substitute_and_released_keeps_their_bringer(
    client: TestClient,
) -> None:
    season, game_id, _bringer = _queue(client)
    absence = client.post(
        "/absences", json={"player_name": "固定甲", "game_id": game_id}
    ).json()
    client.put(f"/absences/{absence['id']}/substitute", json={"player_name": "朋友丁"})

    client.post(f"/absences/{absence['id']}/cancel")

    assert _tags(client, season)["queue"] == {"朋友丁": "會員丙"}


def test_several_bringers_across_games_each_keep_their_own_tag(
    client: TestClient,
) -> None:
    club = create_club(client, name="多人報名")
    season = start_season(
        client,
        club_id=club["id"],
        organizer_token=club["organizer_token"],
        member_names=["固定甲"],
        capacity=1,
        game_dates=["2031-01-07", "2031-01-14"],
    )
    games = [g["id"] for g in season["games"]]
    expected: dict[int, dict[str, str]] = {g: {} for g in games}
    for name in ["會員一", "會員二", "會員三"]:
        headers = auth_headers(identify(client, name)["token"])
        join_club(client, club["id"], headers)
        for game_id in games:
            guest = f"{name}的朋友"
            client.post(
                f"/games/{game_id}/drop-ins",
                json={"people": [{"player_name": guest, "gender": "male"}]},
                headers=headers,
            )
            expected[game_id][guest] = name
    # The organizer adds one too, and somebody leaves the queue.
    client.post(
        f"/games/{games[0]}/drop-ins",
        json={"people": [{"player_name": "管理員的朋友", "gender": "male"}]},
    )
    expected[games[0]]["管理員的朋友"] = "Test Organizer"

    detail = client.get(f"/seasons/{season['id']}").json()["games"]

    for game in detail:
        shown = {
            w["player_name"]: w["brought_by_name"] for w in game["waitlist_entries"]
        }
        assert shown == expected[game["id"]]
