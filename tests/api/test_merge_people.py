"""One name, one person in a club: a typed name that exists is that person,
and duplicates made before that rule can be merged by the organizer."""

from typing import Any

from fastapi.testclient import TestClient

from tests.api.factories import (
    auth_headers,
    create_club,
    identify,
    join_club,
    start_season,
)


def _club(client: TestClient) -> tuple[dict[str, Any], dict[str, Any]]:
    club = create_club(client, name="合併")
    season = start_season(
        client,
        club_id=club["id"],
        organizer_token=club["organizer_token"],
        member_names=["固定甲"],
        game_dates=["2031-01-07", "2031-01-14"],
    )
    return club, season


def _bring(client: TestClient, game_id: int, name: str) -> dict[str, Any]:
    result: dict[str, Any] = client.post(
        f"/games/{game_id}/drop-ins",
        json={"people": [{"player_name": name, "gender": "male"}]},
    ).json()["results"][0]
    return result


def _member_names(client: TestClient, club_id: int) -> list[str]:
    return [m["name"] for m in client.get(f"/clubs/{club_id}/members").json()]


def test_typing_a_name_already_in_the_club_signs_up_that_same_person(
    client: TestClient,
) -> None:
    club, season = _club(client)
    first_game, second_game = (g["id"] for g in season["games"])
    first = _bring(client, first_game, "小明")

    second = client.post(
        f"/games/{second_game}/drop-ins",
        json={"people": [{"player_name": " 小明 ", "gender": "male"}]},
    ).json()["results"][0]

    assert second["player_id"] == first["player_id"]
    assert _member_names(client, club["id"]).count("小明") == 1


def test_merging_moves_signups_and_money_to_the_person_kept(
    client: TestClient,
) -> None:
    club, season = _club(client)
    first_game, second_game = (g["id"] for g in season["games"])
    keep = _bring(client, first_game, "阿德")["player_id"]
    duplicate = _bring(client, second_game, "阿德仔")["player_id"]
    client.post(
        f"/clubs/{club['id']}/players/{duplicate}/payments", json={"amount": "100"}
    )

    response = client.post(
        f"/clubs/{club['id']}/players/{keep}/merge", json={"duplicate_id": duplicate}
    )

    assert response.status_code == 200
    games = client.get(f"/seasons/{season['id']}").json()["games"]
    assert [d["player_name"] for d in games[1]["confirmed_drop_ins"]] == ["阿德"]
    ledger = client.get(f"/clubs/{club['id']}/players/{keep}/ledger").json()
    types = sorted(e["entry_type"] for e in ledger["entries"])
    assert types == ["drop_in_fee_charged", "drop_in_fee_charged", "payment"]
    assert "阿德仔" not in _member_names(client, club["id"])


def test_merging_is_refused_where_both_are_down_for_the_same_game(
    client: TestClient,
) -> None:
    club, season = _club(client)
    first_game = season["games"][0]["id"]
    keep = _bring(client, first_game, "阿德")["player_id"]
    duplicate = _bring(client, first_game, "阿德仔")["player_id"]

    response = client.post(
        f"/clubs/{club['id']}/players/{keep}/merge", json={"duplicate_id": duplicate}
    )

    assert response.status_code == 400
    assert "阿德仔" in _member_names(client, club["id"])


def test_someone_with_their_own_account_cannot_be_merged_away(
    client: TestClient,
) -> None:
    club, season = _club(client)
    keep = _bring(client, season["games"][0]["id"], "阿德")["player_id"]
    linked = identify(client, "有帳號的人")
    join_club(client, club["id"], auth_headers(linked["token"]))

    response = client.post(
        f"/clubs/{club['id']}/players/{keep}/merge",
        json={"duplicate_id": linked["id"]},
    )

    assert response.status_code == 400


def test_merging_repairs_an_old_generated_name_suffix(client: TestClient) -> None:
    club, season = _club(client)
    duplicate = _bring(client, season["games"][0]["id"], "Alice")["player_id"]
    linked = identify(client, "Alice (2)")
    join_club(client, club["id"], auth_headers(linked["token"]))

    response = client.post(
        f"/clubs/{club['id']}/players/{linked['id']}/merge",
        json={"duplicate_id": duplicate},
    )

    assert response.status_code == 200
    assert response.json()["name"] == "Alice"
    assert _member_names(client, club["id"]).count("Alice") == 1


def test_only_the_organizer_can_merge(client: TestClient) -> None:
    club, season = _club(client)
    first_game, second_game = (g["id"] for g in season["games"])
    keep = _bring(client, first_game, "阿德")["player_id"]
    duplicate = _bring(client, second_game, "阿德仔")["player_id"]
    member = auth_headers(identify(client, "Member")["token"])
    join_club(client, club["id"], member)

    response = client.post(
        f"/clubs/{club['id']}/players/{keep}/merge",
        json={"duplicate_id": duplicate},
        headers=member,
    )

    assert response.status_code == 403
