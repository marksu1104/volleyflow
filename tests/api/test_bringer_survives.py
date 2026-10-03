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


def _named_from_the_queue(
    client: TestClient,
) -> tuple[dict[str, Any], int, dict[str, Any], dict[str, Any]]:
    """The sequence production went through on 2026-10-03.

    A full game. 先到的 queues on their own, then 會員丙 queues 朋友丁.
    固定甲 takes leave and the queue fills the slot with 先到的. Then
    朋友丁 is named 固定甲's 代打 straight from the queue, which bumps
    先到的 back out.
    """
    club = create_club(client, name="代打標籤")
    season = start_season(
        client,
        club_id=club["id"],
        organizer_token=club["organizer_token"],
        member_names=["固定甲", "固定乙"],
        capacity=2,
        game_dates=["2031-01-07"],
    )
    season["club_id"] = club["id"]
    game_id = season["games"][0]["id"]
    first = identify(client, "先到的")
    join_club(client, club["id"], auth_headers(first["token"]))
    client.post(
        "/drop-ins",
        json={"player_name": "先到的", "game_id": game_id},
        headers=auth_headers(first["token"]),
    )
    bringer = auth_headers(identify(client, "會員丙")["token"])
    join_club(client, club["id"], bringer)
    client.post(
        f"/games/{game_id}/drop-ins",
        json={"people": [{"player_name": "朋友丁", "gender": "male"}]},
        headers=bringer,
    )
    absence = client.post(
        "/absences", json={"player_name": "固定甲", "game_id": game_id}
    ).json()
    named = client.put(
        f"/absences/{absence['id']}/substitute", json={"player_name": "朋友丁"}
    )
    assert named.status_code == 200, named.text
    game = client.get(f"/seasons/{season['id']}").json()["games"][0]
    sub = next(d for d in game["confirmed_drop_ins"] if d["player_name"] == "朋友丁")
    return season, game_id, absence, sub


def test_a_substitute_released_by_cancelling_the_absence_goes_back_as_queued(
    client: TestClient,
) -> None:
    season, _game_id, absence, _sub = _named_from_the_queue(client)

    client.post(f"/absences/{absence['id']}/cancel")

    assert _tags(client, season)["queue"]["朋友丁"] == "會員丙", (
        "who queued them, not the absent member the 代打 was for"
    )


def test_a_substitute_taken_off_court_goes_back_as_queued(
    client: TestClient,
) -> None:
    season, _game_id, _absence, sub = _named_from_the_queue(client)

    client.post(f"/drop-ins/{sub['id']}/cancel")

    assert _tags(client, season)["queue"].get("朋友丁") == "會員丙"


def _linked_member(client: TestClient, season: dict[str, Any]) -> dict[str, Any]:
    """固定甲, signed in: the roster entry linked to a LINE account."""
    person = identify(client, "固定甲的帳號")
    join_club(client, season["club_id"], auth_headers(person["token"]))
    client.post(
        f"/clubs/{season['club_id']}/players/{season['member_ids'][0]}/link",
        json={"line_player_id": person["id"]},
    )
    return person


def _season_with_a_leave(
    client: TestClient,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    season = start_season(client, member_names=["固定甲"], game_dates=["2031-01-07"])
    member = _linked_member(client, season)
    absence = client.post(
        "/absences",
        json={"player_name": "固定甲", "game_id": season["games"][0]["id"]},
        headers=auth_headers(member["token"]),
    ).json()
    return season, member, absence


def test_a_substitute_the_member_names_is_brought_by_that_member(
    client: TestClient,
) -> None:
    season, member, absence = _season_with_a_leave(client)

    client.put(
        f"/absences/{absence['id']}/substitute",
        json={"player_name": "代打戊"},
        headers=auth_headers(member["token"]),
    )

    assert _tags(client, season)["court"] == {"代打戊": "固定甲"}


def test_a_substitute_the_organizer_names_is_brought_by_the_organizer(
    client: TestClient,
) -> None:
    # Whoever puts somebody on the list answers for them (2026-10-03) —
    # one rule for every signup, 代打 included.
    season, _member, absence = _season_with_a_leave(client)

    client.put(f"/absences/{absence['id']}/substitute", json={"player_name": "代打戊"})

    assert _tags(client, season)["court"] == {"代打戊": "Test Organizer"}


def test_the_member_a_substitute_covers_may_still_take_it_back(
    client: TestClient,
) -> None:
    # The bringer no longer says whose leave it is, so the roster has to
    # find that through the absence — or the member loses 取消代打.
    season, member, absence = _season_with_a_leave(client)
    client.put(f"/absences/{absence['id']}/substitute", json={"player_name": "代打戊"})
    game = client.get(
        f"/seasons/{season['id']}", headers=auth_headers(member["token"])
    ).json()["games"][0]
    [sub] = game["confirmed_drop_ins"]

    cancelled = client.post(
        f"/drop-ins/{sub['id']}/cancel", headers=auth_headers(member["token"])
    )

    assert sub["signed_up_by_me"] is True
    assert cancelled.status_code == 200
