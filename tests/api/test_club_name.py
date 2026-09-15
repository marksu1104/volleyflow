"""A club's name: its organizer can change it, and it has to fit on a phone."""

from fastapi.testclient import TestClient

from tests.api.factories import auth_headers, create_club, identify, join_club


def test_the_organizer_can_rename_their_club(client: TestClient) -> None:
    club = create_club(client, name="舊名字")

    response = client.patch(f"/clubs/{club['id']}", json={"name": "  新名字  "})

    assert response.status_code == 200
    assert response.json()["name"] == "新名字"
    assert client.get(f"/clubs/{club['id']}").json()["name"] == "新名字"


def test_a_member_cannot_rename_the_club(client: TestClient) -> None:
    club = create_club(client, name="舊名字")
    member = auth_headers(identify(client, "Member")["token"])
    join_club(client, club["id"], member)

    response = client.patch(
        f"/clubs/{club['id']}", json={"name": "被改掉"}, headers=member
    )

    assert response.status_code == 403
    assert client.get(f"/clubs/{club['id']}").json()["name"] == "舊名字"


def test_twenty_characters_is_the_most_a_name_can_have(client: TestClient) -> None:
    club = create_club(client)

    at_limit = client.patch(f"/clubs/{club['id']}", json={"name": "一" * 20})
    over = client.patch(f"/clubs/{club['id']}", json={"name": "一" * 21})

    assert at_limit.status_code == 200
    assert over.status_code == 422


def test_a_blank_name_is_refused(client: TestClient) -> None:
    club = create_club(client)

    response = client.patch(f"/clubs/{club['id']}", json={"name": "   "})

    assert response.status_code == 422


def test_a_new_club_is_held_to_the_same_limit(client: TestClient) -> None:
    create_club(client)

    response = client.post("/clubs", json={"name": "一" * 21})

    assert response.status_code == 422
