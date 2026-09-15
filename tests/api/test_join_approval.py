"""Joining by the invite link puts you in a queue; the organizer lets you in."""

from typing import Any

from fastapi.testclient import TestClient

from tests.api.factories import auth_headers, create_club, identify, start_season
from volleyflow.api.invites import invite_token


def _club(client: TestClient) -> tuple[dict[str, Any], dict[str, Any]]:
    club = create_club(client, name="核准")
    season = start_season(
        client,
        club_id=club["id"],
        organizer_token=club["organizer_token"],
        member_names=["固定甲"],
        game_dates=["2031-01-07"],
    )
    return club, season


def _ask(
    client: TestClient,
    club: dict[str, Any],
    name: str,
    wants_fixed: bool | None = None,
) -> tuple[dict[str, Any], dict[str, str], Any]:
    person = identify(client, name)
    headers = auth_headers(person["token"])
    response = client.post(
        f"/clubs/{club['id']}/join",
        json={
            "invite": invite_token(club["id"]),
            "wants_fixed_membership": wants_fixed,
        },
        headers=headers,
    )
    return person, headers, response


def _approve(
    client: TestClient, club: dict[str, Any], player_id: int, fixed: bool
) -> Any:
    return client.post(
        f"/clubs/{club['id']}/members/{player_id}/approve", json={"as_fixed": fixed}
    )


def test_joining_by_link_waits_for_the_organizer(client: TestClient) -> None:
    club, season = _club(client)

    person, headers, joined = _ask(client, club, "新人")

    assert joined.status_code == 200
    mine = client.get(f"/players/{person['id']}/clubs", headers=headers).json()
    assert [(c["id"], c["status"]) for c in mine] == [(club["id"], "pending")]
    refused = client.get(f"/seasons/{season['id']}", headers=headers)
    assert refused.status_code == 403
    assert refused.json()["detail"] == "Waiting for the organizer to approve you"


def test_the_organizer_sees_who_is_waiting_and_what_they_asked_for(
    client: TestClient,
) -> None:
    club, _season = _club(client)
    _ask(client, club, "想固定", wants_fixed=True)
    _ask(client, club, "想臨打", wants_fixed=False)
    me = client.post(
        "/players/identify",
        json={"id_token": club["organizer_token"], "display_name": "Test Organizer"},
    ).json()

    requests = client.get(f"/clubs/{club['id']}/join-requests").json()

    assert [(r["name"], r["wants_fixed_membership"]) for r in requests] == [
        ("想固定", True),
        ("想臨打", False),
    ]
    mine = client.get(f"/players/{me['id']}/clubs").json()
    assert [c["pending_count"] for c in mine] == [2]
    members = [m["name"] for m in client.get(f"/clubs/{club['id']}/members").json()]
    assert "想固定" not in members


def test_approving_lets_them_in(client: TestClient) -> None:
    club, season = _club(client)
    person, headers, _joined = _ask(client, club, "新人")

    approved = _approve(client, club, person["id"], fixed=False)

    assert approved.status_code == 200
    assert client.get(f"/seasons/{season['id']}", headers=headers).status_code == 200
    assert client.get(f"/clubs/{club['id']}/join-requests").json() == []


def test_approved_as_fixed_they_can_be_put_on_the_roster_and_charged(
    client: TestClient,
) -> None:
    club, season = _club(client)
    person, _headers, _joined = _ask(client, club, "新人", wants_fixed=True)
    _approve(client, club, person["id"], fixed=True)

    added = client.post(
        f"/seasons/{season['id']}/members", json={"player_name": "新人"}
    )

    assert added.status_code == 200
    ledger = client.get(f"/clubs/{club['id']}/players/{person['id']}/ledger").json()
    assert any(e["entry_type"] == "season_fee_charged" for e in ledger["entries"])


def test_turning_somebody_away_removes_the_request(client: TestClient) -> None:
    club, _season = _club(client)
    person, headers, _joined = _ask(client, club, "陌生人")

    client.delete(f"/clubs/{club['id']}/members/{person['id']}")

    assert client.get(f"/clubs/{club['id']}/join-requests").json() == []
    assert client.get(f"/players/{person['id']}/clubs", headers=headers).json() == []


def test_asking_twice_says_you_are_already_waiting(client: TestClient) -> None:
    club, _season = _club(client)
    _person, headers, _joined = _ask(client, club, "新人")

    again = client.post(
        f"/clubs/{club['id']}/join",
        json={"invite": invite_token(club["id"])},
        headers=headers,
    )

    assert again.status_code == 400
    assert again.json()["detail"].startswith("Already asked to join")


def test_only_the_organizer_can_approve(client: TestClient) -> None:
    club, _season = _club(client)
    member, member_headers, _joined = _ask(client, club, "會員")
    _approve(client, club, member["id"], fixed=False)
    waiting, _headers, _joined = _ask(client, club, "新人")

    response = client.post(
        f"/clubs/{club['id']}/members/{waiting['id']}/approve",
        json={"as_fixed": False},
        headers=member_headers,
    )

    assert response.status_code == 403
