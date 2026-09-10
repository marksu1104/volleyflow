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

from tests.api.factories import auth_headers, identify, start_season


def _season_past_its_deadline(client: TestClient) -> dict[str, Any]:
    """A game tomorrow with a two-day deadline — so changes closed
    yesterday."""
    tomorrow = date.today() + timedelta(days=1)
    return start_season(
        client,
        member_names=["Alice"],
        capacity=18,
        game_dates=[tomorrow.isoformat(), (tomorrow + timedelta(days=7)).isoformat()],
        change_deadline_days=2,
    )


def _as_alice(client: TestClient, season: dict[str, Any]) -> dict[str, str]:
    """Alice, holding her own LINE account rather than the organizer's."""
    alice = identify(client, "Alice's account")
    client.post(
        f"/clubs/{season['club_id']}/join", headers=auth_headers(alice["token"])
    )
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
    client.post(
        f"/clubs/{season['club_id']}/join", headers=auth_headers(carol["token"])
    )
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


def test_a_season_with_no_deadline_stays_open(client: TestClient) -> None:
    # The default. CLAUDE.md 2.3: the deadline is a configurable
    # parameter, and not setting one means no cut-off at all.
    tomorrow = date.today() + timedelta(days=1)
    season = start_season(
        client,
        member_names=["Alice"],
        capacity=18,
        game_dates=[tomorrow.isoformat()],
        change_deadline_days=None,
    )
    headers = _as_alice(client, season)

    response = client.post(
        "/absences",
        json={"player_name": "Alice", "game_id": season["games"][0]["id"]},
        headers=headers,
    )

    assert response.status_code == 200
