"""Guests brought by a member: who queued them may take them back out, and
the list of people you've brought offers only people still in the club."""

from typing import Any

from fastapi.testclient import TestClient

from tests.api.factories import auth_headers, create_club, identify, start_season
from volleyflow.api.invites import invite_token


def _club(client: TestClient, capacity: int) -> tuple[dict[str, Any], dict[str, Any]]:
    club = create_club(client, name="候補")
    season = start_season(
        client,
        club_id=club["id"],
        organizer_token=club["organizer_token"],
        member_names=["固定甲"],
        capacity=capacity,
        game_dates=["2031-01-07", "2031-01-14"],
    )
    return club, season


def _member(client: TestClient, club: dict[str, Any], name: str) -> dict[str, str]:
    headers = auth_headers(identify(client, name)["token"])
    client.post(
        f"/clubs/{club['id']}/join",
        json={"invite": invite_token(club["id"])},
        headers=headers,
    )
    return headers


def _bring(
    client: TestClient, game_id: int, headers: dict[str, str], name: str = "朋友丙"
) -> dict[str, Any]:
    response = client.post(
        f"/games/{game_id}/drop-ins",
        json={"people": [{"player_name": name, "gender": "male"}]},
        headers=headers,
    )
    result: dict[str, Any] = response.json()["results"][0]
    return result


def test_whoever_queued_a_guest_can_take_them_back_out(client: TestClient) -> None:
    club, season = _club(client, capacity=1)
    member = _member(client, club, "會員乙")
    queued = _bring(client, season["games"][0]["id"], member)
    assert queued["status"] == "waitlisted"

    response = client.post(f"/waitlist/{queued['id']}/cancel", headers=member)

    assert response.status_code == 200


def test_only_the_one_who_queued_a_guest_is_told_they_can_remove_them(
    client: TestClient,
) -> None:
    club, season = _club(client, capacity=1)
    member = _member(client, club, "會員乙")
    other = _member(client, club, "會員丁")
    _bring(client, season["games"][0]["id"], member)

    def queue_seen_by(headers: dict[str, str]) -> list[bool]:
        game = client.get(f"/seasons/{season['id']}", headers=headers).json()["games"][
            0
        ]
        return [w["signed_up_by_me"] for w in game["waitlist_entries"]]

    assert queue_seen_by(member) == [True]
    assert queue_seen_by(other) == [False]


def test_a_guest_promoted_from_the_queue_is_still_theirs_to_cancel(
    client: TestClient,
) -> None:
    club, season = _club(client, capacity=1)
    member = _member(client, club, "會員乙")
    game_id = season["games"][0]["id"]
    _bring(client, game_id, member)

    client.post("/absences", json={"player_name": "固定甲", "game_id": game_id})

    game = client.get(f"/seasons/{season['id']}", headers=member).json()["games"][0]
    promoted = [d for d in game["confirmed_drop_ins"] if d["player_name"] == "朋友丙"]
    assert promoted and promoted[0]["signed_up_by_me"] is True
    cancel = client.post(f"/drop-ins/{promoted[0]['id']}/cancel", headers=member)
    assert cancel.status_code == 200


def test_people_you_have_brought_leave_out_anyone_no_longer_in_the_club(
    client: TestClient,
) -> None:
    club, season = _club(client, capacity=5)
    member = _member(client, club, "會員乙")
    brought = _bring(client, season["games"][0]["id"], member)
    client.post(f"/drop-ins/{brought['id']}/cancel", headers=member)
    client.delete(f"/clubs/{club['id']}/members/{brought['player_id']}")

    guests = client.get(f"/clubs/{club['id']}/my-guests", headers=member).json()

    assert guests == []


def test_picking_someone_no_longer_in_the_club_saves_nobody_and_says_who(
    client: TestClient,
) -> None:
    club, season = _club(client, capacity=5)
    member = _member(client, club, "會員乙")
    first, second = (g["id"] for g in season["games"])
    brought = _bring(client, first, member)
    client.post(f"/drop-ins/{brought['id']}/cancel", headers=member)
    client.delete(f"/clubs/{club['id']}/members/{brought['player_id']}")

    response = client.post(
        f"/games/{second}/drop-ins",
        json={
            "people": [
                {"player_name": "新朋友", "gender": "female"},
                {"player_name": "朋友丙", "player_id": brought["player_id"]},
            ]
        },
        headers=member,
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "朋友丙 is no longer in this club"
    game = client.get(f"/seasons/{season['id']}", headers=member).json()["games"][1]
    assert game["confirmed_drop_ins"] == []


def test_an_id_from_another_club_does_not_reveal_whose_it_is(
    client: TestClient,
) -> None:
    other_club, other_season = _club(client, capacity=5)
    outsider = _bring(
        client, other_season["games"][0]["id"], client.headers, "他隊祕密"
    )
    club, season = _club(client, capacity=5)
    member = _member(client, club, "會員乙")

    response = client.post(
        f"/games/{season['games'][0]['id']}/drop-ins",
        json={"people": [{"player_name": "隨便", "player_id": outsider["player_id"]}]},
        headers=member,
    )

    assert response.status_code == 400
    assert "他隊祕密" not in response.text
