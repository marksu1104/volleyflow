"""When a season may be settled.

Settling locks attendance and writes the refunds, so the games still to
come have to be decided first: it opens three weeks before the last
game, and only once none of the remaining games has an absence nobody
is filling. Asked for on 2026-10-02 — waiting for the final night was
too late when the roster had long been certain.
"""

from datetime import date, timedelta

from fastapi.testclient import TestClient

from tests.api.factories import start_season


def _in(days: int) -> str:
    """Relative to today, because the rule is. A written-down date would
    drift out of (or into) the window as the calendar moves on."""
    return (date.today() + timedelta(days=days)).isoformat()


def test_a_season_more_than_three_weeks_from_its_end_cannot_be_settled(
    client: TestClient,
) -> None:
    season = start_season(client, game_dates=[_in(7), _in(30)])

    response = client.post(f"/seasons/{season['id']}/settle")

    assert response.status_code == 400
    assert response.json()["detail"] == f"Season can be settled from {_in(9)}"


def test_three_weeks_before_the_last_game_it_can(client: TestClient) -> None:
    season = start_season(client, game_dates=[_in(7), _in(21)])

    response = client.post(f"/seasons/{season['id']}/settle")

    assert response.status_code == 200, response.text


def test_an_open_slot_still_to_come_holds_settling_back(client: TestClient) -> None:
    season = start_season(
        client, member_names=["Alice", "Bob"], game_dates=[_in(7), _in(14)]
    )
    client.post(
        "/absences",
        json={"player_name": "Alice", "game_id": season["games"][1]["id"]},
    )

    response = client.post(f"/seasons/{season['id']}/settle")

    assert response.status_code == 400
    assert response.json()["detail"] == f"Game on {_in(14)} has an open slot"


def test_once_somebody_fills_it_the_season_can_be_settled(client: TestClient) -> None:
    season = start_season(
        client, member_names=["Alice", "Bob"], capacity=2, game_dates=[_in(7)]
    )
    game_id = season["games"][0]["id"]
    client.post("/absences", json={"player_name": "Alice", "game_id": game_id})
    client.post("/drop-ins", json={"player_name": "Carol", "game_id": game_id})

    response = client.post(f"/seasons/{season['id']}/settle")

    assert response.status_code == 200, response.text


def test_an_open_slot_on_a_night_already_played_does_not_matter(
    client: TestClient,
) -> None:
    # Nobody can fill it any more; it simply goes unrefunded.
    season = start_season(
        client, member_names=["Alice", "Bob"], game_dates=[_in(-7), _in(7)]
    )
    client.post(
        "/absences",
        json={"player_name": "Alice", "game_id": season["games"][0]["id"]},
    )

    response = client.post(f"/seasons/{season['id']}/settle")

    assert response.status_code == 200, response.text
