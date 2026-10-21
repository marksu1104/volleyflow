"""Correcting which nights a season is down as needing the AC.

The whole point of this endpoint is what it does *not* do. The only
control that existed before was games.set_game_air_conditioning, which
treats a change as the air conditioning having really run and moves
`total_venue_cost` with it. Used to fix a forecast typed in wrongly at
setup, that silently inflated the club's bill one game at a time.

So the first test here is the contract, and the second is the contrast.
"""

from decimal import Decimal
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from tests.api.factories import auth_headers, create_club, identify

DATES = ["2031-08-04", "2031-08-11", "2031-08-18", "2031-08-25"]


def _season(client: TestClient, cooled: list[str]) -> dict[str, Any]:
    """Four games at $20,000, with $630 of air conditioning per cooled
    night — enough games that moving the forecast around changes how each
    one is priced."""
    club = create_club(client)
    response = client.post(
        f"/clubs/{club['id']}/seasons",
        json={
            "total_venue_cost": "20000",
            "ac_surcharge": "630",
            "air_conditioned_dates": cooled,
            "game_dates": DATES,
            "member_names": ["Alice", "Bob"],
            "capacity": 18,
            "minimum_roster": 1,
        },
    )
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    body["club_id"] = club["id"]
    return body


def _detail(client: TestClient, season_id: int) -> dict[str, Any]:
    body: dict[str, Any] = client.get(f"/seasons/{season_id}").json()
    return body


def test_correcting_the_forecast_leaves_the_venue_cost_alone(
    client: TestClient,
) -> None:
    season = _season(client, ["2031-08-04"])
    before = _detail(client, season["id"])

    response = client.put(
        f"/seasons/{season['id']}/air-conditioned-dates",
        json={"dates": ["2031-08-11", "2031-08-18"]},
    )

    assert response.status_code == 204, response.text
    after = _detail(client, season["id"])
    assert after["total_venue_cost"] == before["total_venue_cost"], (
        "the club's bill did not change — only which nights it covers"
    )
    assert [g["air_conditioned"] for g in after["games"]] == [False, True, True, False]


def test_the_per_game_switch_still_moves_the_venue_cost(client: TestClient) -> None:
    # The other control, for contrast: the AC really ran that night, so
    # the venue really charges for it.
    season = _season(client, [])
    before = Decimal(_detail(client, season["id"])["total_venue_cost"])

    response = client.put(
        f"/games/{season['games'][0]['id']}/air-conditioning",
        json={"air_conditioned": True},
    )

    assert response.status_code == 200, response.text
    after = Decimal(_detail(client, season["id"])["total_venue_cost"])
    assert after - before == Decimal("630")


def test_a_corrected_forecast_reprices_the_games(client: TestClient) -> None:
    """The total stays put, so the air-conditioning money has to come out
    of the same pot — the cooled night costs more and every other night
    costs less than it did."""
    season = _season(client, ["2031-08-04"])

    client.put(
        f"/seasons/{season['id']}/air-conditioned-dates",
        json={"dates": ["2031-08-25"]},
    )

    games = _detail(client, season["id"])["games"]
    shares = {g["date"]: Decimal(g["share"]) for g in games}
    assert shares["2031-08-25"] > shares["2031-08-04"], "the cooled night costs more"
    assert shares["2031-08-04"] == shares["2031-08-11"] == shares["2031-08-18"]


def _fee_charged(client: TestClient, club_id: int, player_id: int) -> Decimal:
    """What the ledger says this player owes in season fees, as a positive
    number."""
    entries = client.get(f"/clubs/{club_id}/players/{player_id}/ledger").json()[
        "entries"
    ]
    return -sum(
        (
            Decimal(e["amount"])
            for e in entries
            if e["entry_type"] == "season_fee_charged"
        ),
        start=Decimal("0"),
    )


def test_the_ledger_follows_the_repricing(client: TestClient) -> None:
    """Each game's share is rounded up on its own, so moving the AC to a
    different night lands the rounding differently and a member's
    whole-season fee shifts by a dollar or two. The ledger has to follow,
    or the books and the season disagree about what was charged.
    """
    season = _season(client, ["2031-08-04"])
    member_id = _detail(client, season["id"])["members"][0]["id"]
    before = _fee_charged(client, season["club_id"], member_id)

    client.put(
        f"/seasons/{season['id']}/air-conditioned-dates",
        json={"dates": ["2031-08-04", "2031-08-11"]},
    )

    games = _detail(client, season["id"])["games"]
    expected = sum((Decimal(g["share"]) for g in games), start=Decimal("0"))
    assert before != expected, (
        "the setup has to actually move the fee, or this proves nothing"
    )
    assert _fee_charged(client, season["club_id"], member_id) == expected


def test_a_settled_season_refuses_the_correction(
    client: TestClient, db_session: Session
) -> None:
    # Marked settled directly: a season can only be settled from three
    # weeks before its last game, and these games are still to come.
    season = _season(client, ["2031-08-04"])
    db_session.execute(
        text("UPDATE seasons SET settled_at = CURRENT_TIMESTAMP WHERE id = :s"),
        {"s": season["id"]},
    )
    db_session.commit()

    response = client.put(
        f"/seasons/{season['id']}/air-conditioned-dates",
        json={"dates": ["2031-08-11"]},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Season is already settled"


def test_a_date_with_no_game_is_refused(client: TestClient) -> None:
    # Silently ignoring it would let a typo look like it saved.
    season = _season(client, [])

    response = client.put(
        f"/seasons/{season['id']}/air-conditioned-dates",
        json={"dates": ["2026-12-25"]},
    )

    assert response.status_code == 400
    assert "2026-12-25" in response.json()["detail"]


def test_only_the_organizer_may_correct_it(client: TestClient) -> None:
    season = _season(client, [])
    stranger = identify(client, "Stranger")

    response = client.put(
        f"/seasons/{season['id']}/air-conditioned-dates",
        json={"dates": ["2031-08-04"]},
        headers=auth_headers(stranger["token"]),
    )

    assert response.status_code == 403


def test_an_empty_list_turns_every_night_off(client: TestClient) -> None:
    season = _season(client, DATES)

    response = client.put(
        f"/seasons/{season['id']}/air-conditioned-dates", json={"dates": []}
    )

    assert response.status_code == 204
    games = _detail(client, season["id"])["games"]
    assert [g["air_conditioned"] for g in games] == [False, False, False, False]


def _played_and_coming(client: TestClient) -> dict[str, Any]:
    """Two games already played and two still to come, the first of
    each cooled."""
    club = create_club(client)
    response = client.post(
        f"/clubs/{club['id']}/seasons",
        json={
            "total_venue_cost": "20000",
            "ac_surcharge": "630",
            "air_conditioned_dates": ["2020-01-07", "2031-01-07"],
            "game_dates": ["2020-01-07", "2020-01-14", "2031-01-07", "2031-01-14"],
            "member_names": ["Alice", "Bob"],
            "capacity": 18,
            "minimum_roster": 1,
        },
    )
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def test_a_played_games_forecast_cannot_be_corrected(client: TestClient) -> None:
    # What happened on a night already played is recorded with the
    # per-game switch, which moves the bill. Correcting its forecast too
    # would adjust the same night twice.
    season = _played_and_coming(client)

    response = client.put(
        f"/seasons/{season['id']}/air-conditioned-dates",
        json={"dates": ["2020-01-14", "2031-01-07"]},
    )

    assert response.status_code == 400
    assert "2020-01-07" in response.json()["detail"]
    after = _detail(client, season["id"])
    assert [g["air_conditioned"] for g in after["games"]] == [True, False, True, False]


def test_games_still_to_come_can_be_corrected_around_played_ones(
    client: TestClient,
) -> None:
    season = _played_and_coming(client)

    response = client.put(
        f"/seasons/{season['id']}/air-conditioned-dates",
        json={"dates": ["2020-01-07", "2031-01-14"]},
    )

    assert response.status_code == 204, response.text
    after = _detail(client, season["id"])
    assert [g["air_conditioned"] for g in after["games"]] == [True, False, False, True]


def test_the_preview_writes_nothing(client: TestClient) -> None:
    season = _season(client, ["2031-08-04"])
    before = _detail(client, season["id"])

    response = client.post(
        f"/seasons/{season['id']}/air-conditioned-dates/preview",
        json={"dates": ["2031-08-11", "2031-08-18"]},
    )

    assert response.status_code == 200, response.text
    assert _detail(client, season["id"]) == before


def test_the_preview_quotes_what_saving_then_charges(client: TestClient) -> None:
    # The figures come from the same pricing code as the books, so what
    # the organizer is shown is exactly what the ledger then says.
    season = _season(client, ["2031-08-04"])
    wanted = ["2031-08-11", "2031-08-18"]

    preview = client.post(
        f"/seasons/{season['id']}/air-conditioned-dates/preview", json={"dates": wanted}
    ).json()
    client.put(f"/seasons/{season['id']}/air-conditioned-dates", json={"dates": wanted})

    after = _detail(client, season["id"])
    assert [Decimal(g["share_after"]) for g in preview["games"]] == [
        Decimal(g["share"]) for g in after["games"]
    ]
    cooled = [g["air_conditioned"] for g in preview["games"]]
    assert cooled == [False, True, True, False]
    alice = next(m for m in after["members"] if m["name"] == "Alice")
    ledger = client.get(
        f"/clubs/{season['club_id']}/players/{alice['id']}/ledger"
    ).json()
    assert -Decimal(ledger["balance"]) == Decimal(preview["season_fee_after"])


def test_the_preview_refuses_a_date_with_no_game(client: TestClient) -> None:
    season = _season(client, [])

    response = client.post(
        f"/seasons/{season['id']}/air-conditioned-dates/preview",
        json={"dates": ["2031-09-01"]},
    )

    assert response.status_code == 400
