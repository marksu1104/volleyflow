"""The people you have brought before, offered back as a tap.

Typing a friend's name again every week is how one real person becomes
three rows in the players table under three spellings, each carrying its
own money. This is the list the pickers offer instead — asked for on
2026-09-10, along with the worry that it would grow into something
nobody can scan.
"""

from typing import Any

from fastapi.testclient import TestClient

from tests.api.factories import auth_headers, identify, start_season


def _member(client: TestClient, season: dict[str, Any], name: str) -> dict[str, Any]:
    person = identify(client, name)
    client.post(
        f"/clubs/{season['club_id']}/join", headers=auth_headers(person["token"])
    )
    return person


def _bring(
    client: TestClient, game_id: int, host: dict[str, Any], guest_name: str
) -> None:
    client.post(
        f"/games/{game_id}/drop-ins",
        json={"people": [{"player_name": guest_name, "gender": "male"}]},
        headers=auth_headers(host["token"]),
    )


def test_it_lists_the_people_this_caller_brought(client: TestClient) -> None:
    season = start_season(client, member_names=["Alice"], capacity=18)
    host = _member(client, season, "Host")
    _bring(client, season["games"][0]["id"], host, "阿哲")

    guests = client.get(
        f"/clubs/{season['club_id']}/my-guests", headers=auth_headers(host["token"])
    ).json()

    assert [g["name"] for g in guests] == ["阿哲"]
    assert guests[0]["times"] == 1


def test_somebody_elses_guests_are_not_mine(client: TestClient) -> None:
    # The list is per person: it exists so *I* can re-pick the friends *I*
    # keep bringing, and another member's friends are no help.
    season = start_season(client, member_names=["Alice"], capacity=18)
    host = _member(client, season, "Host")
    other = _member(client, season, "Other")
    _bring(client, season["games"][0]["id"], host, "阿哲")

    guests = client.get(
        f"/clubs/{season['club_id']}/my-guests", headers=auth_headers(other["token"])
    ).json()

    assert guests == []


def test_typing_the_same_name_twice_makes_two_different_people(
    client: TestClient,
) -> None:
    """The behaviour this picker exists to route around.

    A typed name always means *a new person* — deliberately, because two
    real people called 小明 must both be able to play and one of them
    inheriting the other's ledger is not recoverable. The cost is that
    bringing the same friend week after week by typing their name grows
    a new row every time, each with its own money.

    So: type once, pick thereafter.
    """
    season = start_season(client, member_names=["Alice"], capacity=18)
    host = _member(client, season, "Host")
    _bring(client, season["games"][0]["id"], host, "阿哲")
    _bring(client, season["games"][1]["id"], host, "阿哲")

    guests = client.get(
        f"/clubs/{season['club_id']}/my-guests", headers=auth_headers(host["token"])
    ).json()

    assert len(guests) == 2, "two rows, one name — this is why picking matters"
    assert {g["name"] for g in guests} == {"阿哲"}


def test_picking_the_same_person_again_keeps_them_one_person(
    client: TestClient,
) -> None:
    # The intended flow: bring them once by name, then pick them from
    # this list, which sends their player_id. One row, a count that goes
    # up, and one ledger.
    season = start_season(client, member_names=["Alice"], capacity=18)
    host = _member(client, season, "Host")
    _bring(client, season["games"][0]["id"], host, "阿哲")
    known = client.get(
        f"/clubs/{season['club_id']}/my-guests", headers=auth_headers(host["token"])
    ).json()[0]

    client.post(
        f"/games/{season['games'][1]['id']}/drop-ins",
        json={"people": [{"player_name": known["name"], "player_id": known["id"]}]},
        headers=auth_headers(host["token"]),
    )

    guests = client.get(
        f"/clubs/{season['club_id']}/my-guests", headers=auth_headers(host["token"])
    ).json()
    assert len(guests) == 1
    assert guests[0]["times"] == 2
    assert guests[0]["last_played"] is not None


def test_the_most_recent_come_first(client: TestClient) -> None:
    season = start_season(client, member_names=["Alice"], capacity=18)
    host = _member(client, season, "Host")
    _bring(client, season["games"][0]["id"], host, "先來的")
    _bring(client, season["games"][1]["id"], host, "後來的")

    guests = client.get(
        f"/clubs/{season['club_id']}/my-guests", headers=auth_headers(host["token"])
    ).json()

    assert [g["name"] for g in guests] == ["後來的", "先來的"]


def test_signing_yourself_up_does_not_put_you_on_your_own_list(
    client: TestClient,
) -> None:
    season = start_season(client, member_names=["Alice"], capacity=18)
    host = _member(client, season, "Host")
    client.post(
        f"/games/{season['games'][0]['id']}/drop-ins",
        json={"people": [{"player_name": host["name"], "player_id": host["id"]}]},
        headers=auth_headers(host["token"]),
    )

    guests = client.get(
        f"/clubs/{season['club_id']}/my-guests", headers=auth_headers(host["token"])
    ).json()

    assert guests == []


def test_somebody_outside_the_club_gets_nothing(client: TestClient) -> None:
    season = start_season(client, member_names=["Alice"], capacity=18)
    outsider = identify(client, "Outsider")

    response = client.get(
        f"/clubs/{season['club_id']}/my-guests",
        headers=auth_headers(outsider["token"]),
    )

    assert response.status_code == 403
