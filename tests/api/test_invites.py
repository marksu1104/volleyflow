"""The invite-link token: GET /clubs/{id}/invite and GET /invites/{token}.

See src/volleyflow/api/invites.py for why a raw club id in the shared
link was replaced with one of these.
"""

from fastapi.testclient import TestClient

from tests.api.factories import auth_headers, create_club, identify


def test_organizer_can_read_their_clubs_invite_token(client: TestClient) -> None:
    club = create_club(client, "啪排郎")

    response = client.get(f"/clubs/{club['id']}/invite")

    assert response.status_code == 200
    body = response.json()
    assert body["club_id"] == club["id"]
    assert body["club_name"] == "啪排郎"
    assert body["token"]


def test_a_member_cannot_read_the_invite_token(client: TestClient) -> None:
    club = create_club(client)
    member = identify(client, "Carol")
    client.post(f"/clubs/{club['id']}/join", headers=auth_headers(member["token"]))

    response = client.get(
        f"/clubs/{club['id']}/invite", headers=auth_headers(member["token"])
    )

    assert response.status_code == 403


def test_the_token_resolves_to_the_club_it_names(client: TestClient) -> None:
    club = create_club(client, "啪排郎")
    token = client.get(f"/clubs/{club['id']}/invite").json()["token"]

    response = client.get(f"/invites/{token}")

    assert response.status_code == 200
    body = response.json()
    assert body["club_id"] == club["id"]
    assert body["club_name"] == "啪排郎"
    assert body["token"] is None, "no need to echo it back"


def test_resolving_needs_no_identity_at_all(client: TestClient) -> None:
    """The whole point: a visitor with no LINE session yet still needs to
    see whose invite this is, before signing in."""
    club = create_club(client)
    token = client.get(f"/clubs/{club['id']}/invite").json()["token"]

    response = client.get(f"/invites/{token}", headers={})

    assert response.status_code == 200


def test_a_forged_token_is_refused(client: TestClient) -> None:
    club = create_club(client)
    real = client.get(f"/clubs/{club['id']}/invite").json()["token"]
    forged = real[:-1] + ("0" if real[-1] != "0" else "1")

    response = client.get(f"/invites/{forged}")

    assert response.status_code == 404


def test_a_malformed_token_is_refused_not_500(client: TestClient) -> None:
    for garbage in ["not-a-token", "12345", "", "abc.def", "12.34.56"]:
        response = client.get(f"/invites/{garbage}")
        assert response.status_code == 404, garbage


def test_two_clubs_get_different_tokens(client: TestClient) -> None:
    a = create_club(client, "球隊 A")
    token_a = client.get(f"/clubs/{a['id']}/invite").json()["token"]
    b = create_club(client, "球隊 B")
    token_b = client.get(f"/clubs/{b['id']}/invite").json()["token"]

    assert token_a != token_b
    # And each still resolves to its own club, not the other one.
    assert client.get(f"/invites/{token_a}").json()["club_id"] == a["id"]
    assert client.get(f"/invites/{token_b}").json()["club_id"] == b["id"]
