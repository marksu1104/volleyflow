"""The invite-link token: GET /clubs/{id}/invite and GET /invites/{token}.

See src/volleyflow/api/invites.py for why a raw club id in the shared
link was replaced with one of these.
"""

import hashlib
import hmac

import pytest
from fastapi.testclient import TestClient

from tests.api.factories import auth_headers, create_club, identify
from volleyflow.api.invites import invite_token


def test_organizer_can_read_their_clubs_invite_token(client: TestClient) -> None:
    club = create_club(client, "晴光館")

    response = client.get(f"/clubs/{club['id']}/invite")

    assert response.status_code == 200
    body = response.json()
    assert body["club_id"] == club["id"]
    assert body["club_name"] == "晴光館"
    assert body["token"]


def test_a_member_cannot_read_the_invite_token(client: TestClient) -> None:
    club = create_club(client)
    member = identify(client, "Carol")
    client.post(
        f"/clubs/{club['id']}/join",
        json={"invite": invite_token(club["id"])},
        headers=auth_headers(member["token"]),
    )

    response = client.get(
        f"/clubs/{club['id']}/invite", headers=auth_headers(member["token"])
    )

    assert response.status_code == 403


def test_the_token_resolves_to_the_club_it_names(client: TestClient) -> None:
    club = create_club(client, "晴光館")
    token = client.get(f"/clubs/{club['id']}/invite").json()["token"]

    response = client.get(f"/invites/{token}")

    assert response.status_code == 200
    body = response.json()
    assert body["club_id"] == club["id"]
    assert body["club_name"] == "晴光館"
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


def _signed_with_the_public_fallback(club_id: int) -> str:
    """A token signed with the key that is written in invites.py, which
    anybody can read on GitHub."""
    mac = hmac.new(
        b"volleyflow-dev-invite-secret", str(club_id).encode(), hashlib.sha256
    ).hexdigest()[:20]
    return f"{club_id}.{mac}"


def test_a_token_signed_with_the_public_fallback_is_refused(
    client: TestClient,
) -> None:
    """The production finding of 2026-09-15, as a test: a token signed with
    the fallback in this repository was sent to the live API and accepted,
    because the real secret had never been set there. With a secret of its
    own configured, the public one proves nothing."""
    club = create_club(client)

    response = client.get(f"/invites/{_signed_with_the_public_fallback(club['id'])}")

    assert response.status_code == 404
    assert response.json()["detail"] == "Invite link not recognised"


def test_without_a_secret_invites_refuse_instead_of_using_the_public_one(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Fail closed: a missing secret has to be a visibly broken invite
    # screen, not a working one that protects nothing.
    club = create_club(client)
    monkeypatch.delenv("INVITE_TOKEN_SECRET", raising=False)
    monkeypatch.delenv("VOLLEYFLOW_DEV_LOGIN", raising=False)

    issued = client.get(f"/clubs/{club['id']}/invite")
    resolved = client.get(f"/invites/{_signed_with_the_public_fallback(club['id'])}")

    assert issued.status_code == 503
    assert resolved.status_code == 503, (
        "and above all, the forged token is not accepted"
    )
    assert issued.json()["detail"] == "Invite links aren't configured on this server"


def test_local_sign_in_may_use_the_fallback(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A freshly cloned checkout still works without being told a secret —
    # which is the only thing the fallback was ever for.
    club = create_club(client)
    monkeypatch.delenv("INVITE_TOKEN_SECRET", raising=False)
    monkeypatch.setenv("VOLLEYFLOW_DEV_LOGIN", "1")

    token = client.get(f"/clubs/{club['id']}/invite").json()["token"]

    assert client.get(f"/invites/{token}").status_code == 200


def test_two_clubs_get_different_tokens(client: TestClient) -> None:
    a = create_club(client, "球隊 A")
    token_a = client.get(f"/clubs/{a['id']}/invite").json()["token"]
    b = create_club(client, "球隊 B")
    token_b = client.get(f"/clubs/{b['id']}/invite").json()["token"]

    assert token_a != token_b
    # And each still resolves to its own club, not the other one.
    assert client.get(f"/invites/{token_a}").json()["club_id"] == a["id"]
    assert client.get(f"/invites/{token_b}").json()["club_id"] == b["id"]


def test_joining_with_the_clubs_link_works(client: TestClient) -> None:
    club = create_club(client)
    token = client.get(f"/clubs/{club['id']}/invite").json()["token"]
    carol = identify(client, "Carol")

    response = client.post(
        f"/clubs/{club['id']}/join",
        json={"invite": token},
        headers=auth_headers(carol["token"]),
    )

    assert response.status_code == 200


def test_joining_by_id_alone_is_refused(client: TestClient) -> None:
    """The hole the token existed to close, and didn't until 2026-09-15:
    the shared link hid the id, but the endpoint took a bare one from
    anybody signed in."""
    club = create_club(client)
    carol = identify(client, "Carol")

    no_body = client.post(
        f"/clubs/{club['id']}/join", headers=auth_headers(carol["token"])
    )
    forged = client.post(
        f"/clubs/{club['id']}/join",
        json={"invite": f"{club['id']}.00000000000000000000"},
        headers=auth_headers(carol["token"]),
    )

    assert no_body.status_code == 422
    assert forged.status_code == 403


def test_one_clubs_link_does_not_open_another(client: TestClient) -> None:
    # A token is for the club it was made for, not a key to whichever id
    # is in the path beside it.
    mine = create_club(client, "我的球隊")
    my_token = client.get(f"/clubs/{mine['id']}/invite").json()["token"]
    theirs = create_club(client, "別人的球隊")
    carol = identify(client, "Carol")

    response = client.post(
        f"/clubs/{theirs['id']}/join",
        json={"invite": my_token},
        headers=auth_headers(carol["token"]),
    )

    assert response.status_code == 403
