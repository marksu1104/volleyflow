"""What the change deadline actually stops.

Asked for on 2026-09-11: "請假、取消請假、報名都應該只能在更動期限前
做". Every one of those is checked here against a game that has already
passed the deadline, so a route that quietly forgets the check fails.

The organizer is exempt, deliberately and separately tested: the
deadline exists to stop the roster shifting under them on the night, and
they are the one who has to record what actually happened.
"""

from datetime import date, timedelta
from typing import Any

from fastapi.testclient import TestClient

from tests.api.factories import auth_headers, identify, join_club, start_season


def _season_past_its_deadline(client: TestClient) -> dict[str, Any]:
    """A game tomorrow with a two-day deadline — so changes closed
    yesterday."""
    tomorrow = date.today() + timedelta(days=1)
    return start_season(
        client,
        member_names=["Alice"],
        capacity=18,
        game_dates=[tomorrow.isoformat(), (tomorrow + timedelta(days=7)).isoformat()],
        change_deadline_hours=48,
    )


def _as_alice(client: TestClient, season: dict[str, Any]) -> dict[str, str]:
    """Alice, holding her own LINE account rather than the organizer's."""
    alice = identify(client, "Alice's account")
    join_club(client, season["club_id"], auth_headers(alice["token"]))
    client.post(
        f"/clubs/{season['club_id']}/players/{season['member_ids'][0]}/link",
        json={"line_player_id": alice["id"]},
    )
    return auth_headers(alice["token"])


def test_a_member_cannot_take_leave_past_the_deadline(client: TestClient) -> None:
    season = _season_past_its_deadline(client)
    headers = _as_alice(client, season)

    response = client.post(
        "/absences",
        json={"player_name": "Alice", "game_id": season["games"][0]["id"]},
        headers=headers,
    )

    assert response.status_code == 400


def test_a_member_cannot_take_leave_back_past_the_deadline(
    client: TestClient,
) -> None:
    season = _season_past_its_deadline(client)
    # Recorded by the organizer, who is exempt, so there is something to
    # cancel.
    absence = client.post(
        "/absences",
        json={"player_name": "Alice", "game_id": season["games"][0]["id"]},
    ).json()
    headers = _as_alice(client, season)

    response = client.post(f"/absences/{absence['id']}/cancel", headers=headers)

    assert response.status_code == 400


def test_nobody_may_sign_up_past_the_deadline(client: TestClient) -> None:
    season = _season_past_its_deadline(client)
    headers = _as_alice(client, season)

    response = client.post(
        f"/games/{season['games'][0]['id']}/drop-ins",
        json={"people": [{"player_name": "朋友", "gender": "male"}]},
        headers=headers,
    )

    assert response.status_code == 400


def test_a_signup_cannot_be_taken_back_past_the_deadline(
    client: TestClient,
) -> None:
    # Carol cancelling her own — anybody else would be refused for a
    # different reason (403, not theirs to cancel), which would not
    # prove anything about the deadline.
    season = _season_past_its_deadline(client)
    carol = identify(client, "Carol")
    join_club(client, season["club_id"], auth_headers(carol["token"]))
    signup = client.post(
        f"/games/{season['games'][0]['id']}/drop-ins",
        json={"people": [{"player_name": carol["name"], "player_id": carol["id"]}]},
    ).json()["results"][0]

    response = client.post(
        f"/drop-ins/{signup['id']}/cancel", headers=auth_headers(carol["token"])
    )

    assert response.status_code == 400


def test_a_later_game_is_still_open(client: TestClient) -> None:
    # The deadline is per game, not per season: next week is untouched.
    season = _season_past_its_deadline(client)
    headers = _as_alice(client, season)

    response = client.post(
        "/absences",
        json={"player_name": "Alice", "game_id": season["games"][1]["id"]},
        headers=headers,
    )

    assert response.status_code == 200


def test_the_organizer_can_still_record_what_happened(client: TestClient) -> None:
    # Somebody drops out an hour before and a replacement turns up. The
    # deadline protects the organizer from the roster shifting; refusing
    # to let *them* write it down would only make the record wrong.
    season = _season_past_its_deadline(client)

    response = client.post(
        "/absences",
        json={"player_name": "Alice", "game_id": season["games"][0]["id"]},
    )

    assert response.status_code == 200


def test_a_new_season_closes_changes_24_hours_before_the_game(
    client: TestClient,
) -> None:
    # Every season has a deadline now (2026-10-05): the roster status and
    # the promotion notices are sent when it passes.
    response = client.post(
        "/clubs/{}/seasons".format(start_season(client)["club_id"]),
        json={
            "total_venue_cost": "1000",
            "game_dates": ["2031-01-07"],
            "member_names": ["Alice"],
            "capacity": 18,
            "minimum_roster": 12,
        },
    )

    assert response.json()["change_deadline_hours"] == 24


def test_changes_close_the_given_hours_before_the_game_starts(
    client: TestClient,
) -> None:
    # A game an hour and a half away, with a two-hour deadline: closed. The
    # same game with a one-hour deadline: still open.
    from datetime import datetime, timezone

    soon = datetime.now(timezone(timedelta(hours=8))) + timedelta(minutes=90)
    if soon.date() != (soon - timedelta(minutes=90)).date():
        return  # straddles midnight in Taiwan; the date/time split can't say it
    closed = start_season(
        client,
        member_names=["Alice"],
        game_dates=[soon.date().isoformat()],
        game_start_time=soon.strftime("%H:%M"),
        change_deadline_hours=2,
    )
    still_open = start_season(
        client,
        member_names=["Alice"],
        game_dates=[soon.date().isoformat()],
        game_start_time=soon.strftime("%H:%M"),
        change_deadline_hours=1,
        club_id=closed["club_id"],
    )
    alice = _as_alice(client, closed)

    refused = client.post(
        "/absences",
        json={"player_name": "Alice", "game_id": closed["games"][0]["id"]},
        headers=alice,
    )
    allowed = client.post(
        "/absences",
        json={"player_name": "Alice", "game_id": still_open["games"][0]["id"]},
        headers=alice,
    )

    assert refused.status_code == 400
    assert allowed.status_code == 200
