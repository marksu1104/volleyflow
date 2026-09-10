"""Who is allowed to undo whose attendance.

Asked for directly on 2026-09-10: "my guest could become someone else's
代打 and then be cancelled by them?", "could I cancel a signup that isn't
mine?", "the member screen and the management screen must not have the
same powers". Every one of those is a question about the server, because
a hidden button is not a permission — so they are answered here rather
than in the frontend.

The shape of the answer, for the whole file:

    a member  -> their own attendance, and the guests they brought
    organizer -> anything inside their own club
    anybody   -> nothing at all in a club they don't belong to
"""

from typing import Any

from fastapi.testclient import TestClient

from tests.api.factories import auth_headers, identify, start_season


def _club_member(
    client: TestClient, season: dict[str, Any], name: str
) -> dict[str, Any]:
    """An identified person who has joined the club but organizes nothing."""
    person = identify(client, name)
    client.post(
        f"/clubs/{season['club_id']}/join", headers=auth_headers(person["token"])
    )
    return person


def test_a_member_cannot_cancel_a_signup_that_is_not_theirs(
    client: TestClient,
) -> None:
    season = start_season(client, member_names=["Alice"], capacity=18)
    game_id = season["games"][0]["id"]
    stranger = _club_member(client, season, "Stranger")
    theirs = client.post(
        "/drop-ins", json={"player_name": "Carol", "game_id": game_id}
    ).json()

    response = client.post(
        f"/drop-ins/{theirs['id']}/cancel", headers=auth_headers(stranger["token"])
    )

    assert response.status_code == 403


def test_a_member_can_cancel_a_guest_they_brought(client: TestClient) -> None:
    # The guest has no account and never will: the member who brought
    # them is the only person besides the organizer who can act for them.
    season = start_season(client, member_names=["Alice"], capacity=18)
    game_id = season["games"][0]["id"]
    host = _club_member(client, season, "Host")
    brought = client.post(
        f"/games/{game_id}/drop-ins",
        json={"people": [{"player_name": "Host's friend", "gender": "male"}]},
        headers=auth_headers(host["token"]),
    ).json()["results"][0]

    response = client.post(
        f"/drop-ins/{brought['id']}/cancel", headers=auth_headers(host["token"])
    )

    assert response.status_code == 200


def test_a_member_can_cancel_the_substitute_they_arranged(client: TestClient) -> None:
    # 指定代打 is offered on the member's own screen, so undoing it has
    # to work there too — the substitute is a different person, and a
    # naive "self only" check refuses the very member who arranged them.
    season = start_season(client, member_names=["Alice"], capacity=18)
    game_id = season["games"][0]["id"]
    alice = _club_member(client, season, "Alice's account")
    client.post(
        f"/clubs/{season['club_id']}/players/{season['member_ids'][0]}/link",
        json={"line_player_id": alice["id"]},
    )
    absence = client.post(
        "/absences",
        json={"player_name": "Alice", "game_id": game_id},
        headers=auth_headers(alice["token"]),
    ).json()
    substitute = client.put(
        f"/absences/{absence['id']}/substitute",
        json={"player_name": "Zoe"},
        headers=auth_headers(alice["token"]),
    ).json()

    response = client.post(
        f"/drop-ins/{substitute['id']}/cancel", headers=auth_headers(alice["token"])
    )

    assert response.status_code == 200


def test_a_member_cannot_cancel_a_substitute_somebody_else_arranged(
    client: TestClient,
) -> None:
    season = start_season(client, member_names=["Alice", "Bob"], capacity=18)
    game_id = season["games"][0]["id"]
    meddler = _club_member(client, season, "Meddler")
    absence = client.post(
        "/absences", json={"player_name": "Alice", "game_id": game_id}
    ).json()
    substitute = client.put(
        f"/absences/{absence['id']}/substitute", json={"player_name": "Zoe"}
    ).json()

    response = client.post(
        f"/drop-ins/{substitute['id']}/cancel", headers=auth_headers(meddler["token"])
    )

    assert response.status_code == 403


def test_a_member_cannot_arrange_a_substitute_for_somebody_elses_absence(
    client: TestClient,
) -> None:
    season = start_season(client, member_names=["Alice"], capacity=18)
    game_id = season["games"][0]["id"]
    meddler = _club_member(client, season, "Meddler")
    absence = client.post(
        "/absences", json={"player_name": "Alice", "game_id": game_id}
    ).json()

    response = client.put(
        f"/absences/{absence['id']}/substitute",
        json={"player_name": "Zoe"},
        headers=auth_headers(meddler["token"]),
    )

    assert response.status_code == 403


def test_a_member_cannot_take_somebody_else_off_the_waitlist(
    client: TestClient,
) -> None:
    season = start_season(client, member_names=["Alice"], capacity=1)
    game_id = season["games"][0]["id"]
    meddler = _club_member(client, season, "Meddler")
    queued = client.post(
        "/drop-ins", json={"player_name": "Carol", "game_id": game_id}
    ).json()
    assert queued["status"] == "waitlisted"

    response = client.post(
        f"/waitlist/{queued['id']}/cancel", headers=auth_headers(meddler["token"])
    )

    assert response.status_code == 403


def test_only_the_organizer_may_reorder_the_queue_or_the_roster(
    client: TestClient,
) -> None:
    season = start_season(client, member_names=["Alice"], capacity=1)
    game_id = season["games"][0]["id"]
    member = _club_member(client, season, "Ordinary")
    queued = client.post(
        "/drop-ins", json={"player_name": "Carol", "game_id": game_id}
    ).json()
    as_member = auth_headers(member["token"])

    assert (
        client.post(
            f"/waitlist/{queued['id']}/promote", json={}, headers=as_member
        ).status_code
        == 403
    )
    assert (
        client.post(
            f"/seasons/{season['id']}/members",
            json={"player_name": "Someone"},
            headers=as_member,
        ).status_code
        == 403
    )
    assert (
        client.delete(
            f"/seasons/{season['id']}/members/{season['member_ids'][0]}",
            headers=as_member,
        ).status_code
        == 403
    )
    assert (
        client.post(
            f"/games/{game_id}/cancel", json={"refunded": True}, headers=as_member
        ).status_code
        == 403
    )
    assert (
        client.patch(
            f"/seasons/{season['id']}", json={"capacity": 5}, headers=as_member
        ).status_code
        == 403
    )


def test_somebody_outside_the_club_sees_and_touches_nothing(
    client: TestClient,
) -> None:
    season = start_season(client, member_names=["Alice"], capacity=18)
    game_id = season["games"][0]["id"]
    outsider = identify(client, "Outsider")  # identified, but never joined
    as_outsider = auth_headers(outsider["token"])

    assert (
        client.get(f"/seasons/{season['id']}", headers=as_outsider).status_code == 403
    )
    assert (
        client.post(
            "/absences",
            json={"player_name": "Alice", "game_id": game_id},
            headers=as_outsider,
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/drop-ins",
            json={"player_name": "Outsider", "game_id": game_id},
            headers=as_outsider,
        ).status_code
        == 403
    )
