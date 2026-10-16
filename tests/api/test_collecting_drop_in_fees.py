"""Taking a drop-in's fee one night at a time, from whoever actually played.

Reported 2026-10-16 from real use, three problems on the same screen:

* 已收 on one night of 當天臨打 marked every other night paid too — the
  row showed and collected the person's whole season of drop-in fees;
* a fee could not be marked received at all for somebody who had since
  been taken out of the club (「○○不是隊伍裡的人」);
* and, asked alongside: a fee is only ever taken from somebody who got a
  place that night, never from a queued signup.
"""

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.api.factories import start_season
from volleyflow.db.models import ClubMemberRow


def _future(days: int) -> str:
    return (
        datetime.now(ZoneInfo("Asia/Taipei")).date() + timedelta(days=days)
    ).isoformat()


def _two_nights_of_one_guest(
    client: TestClient,
) -> tuple[dict[str, Any], int, int, int]:
    season = start_season(
        client, member_names=["Alice"], capacity=2, game_dates=[_future(7), _future(14)]
    )
    first, second = (g["id"] for g in season["games"])
    guest = None
    for game_id in (first, second):
        signup = client.post(
            "/drop-ins", json={"player_name": "訪客甲", "game_id": game_id}
        ).json()
        assert signup["status"] == "confirmed", signup
        guest = signup["player_id"]
    assert guest is not None
    return season, first, second, guest


def _nights(
    client: TestClient, season: dict[str, Any], player_id: int
) -> dict[int, tuple[Decimal, Decimal]]:
    rows = client.get(
        f"/clubs/{season['club_id']}/balances?season_id={season['id']}"
    ).json()
    row = next(r for r in rows if r["player_id"] == player_id)
    return {
        n["game_id"]: (Decimal(n["charged"]), Decimal(n["paid"])) for n in row["nights"]
    }


def _pay(
    client: TestClient,
    season: dict[str, Any],
    player_id: int,
    amount: Decimal,
    game_id: int,
) -> Any:
    return client.post(
        f"/clubs/{season['club_id']}/players/{player_id}/payments",
        json={"amount": str(amount), "game_id": game_id},
    )


def test_paying_for_one_night_leaves_the_other_night_unpaid(client: TestClient) -> None:
    season, first, second, guest = _two_nights_of_one_guest(client)
    charged = _nights(client, season, guest)[first][0]

    paid = _pay(client, season, guest, charged, first)

    assert paid.status_code == 200, paid.text
    assert paid.json()["game_id"] == first
    assert paid.json()["season_id"] == season["id"], "the night sets the season"
    nights = _nights(client, season, guest)
    assert nights[first] == (charged, charged)
    assert nights[second][1] == 0, "the other night is still owed"


def test_undoing_a_nights_payment_reopens_that_night(client: TestClient) -> None:
    season, first, _second, guest = _two_nights_of_one_guest(client)
    charged = _nights(client, season, guest)[first][0]
    entry = _pay(client, season, guest, charged, first).json()

    undone = client.post(f"/ledger-entries/{entry['id']}/reverse", json={})

    assert undone.status_code == 200, undone.text
    assert undone.json()["game_id"] == first
    assert _nights(client, season, guest)[first][1] == 0


def test_a_queued_signup_is_never_charged_or_collected_from(client: TestClient) -> None:
    season = start_season(
        client, member_names=["Alice"], capacity=1, game_dates=[_future(7)]
    )
    game_id = season["games"][0]["id"]
    queued = client.post(
        "/drop-ins", json={"player_name": "候補的人", "game_id": game_id}
    ).json()
    assert queued["status"] == "waitlisted"

    taken = _pay(client, season, queued["player_id"], Decimal(100), game_id)

    assert taken.status_code == 400
    assert "isn't on court" in taken.json()["detail"]
    rows = client.get(
        f"/clubs/{season['club_id']}/balances?season_id={season['id']}"
    ).json()
    assert all(r["player_id"] != queued["player_id"] for r in rows), (
        "nothing on the books"
    )


def test_a_fee_can_still_be_taken_from_somebody_no_longer_in_the_club(
    client: TestClient, db_session: Session
) -> None:
    """The guest had played and been taken out of the club since — made here
    directly, because the API now refuses to take out somebody who still
    owes (below). Her fee is still owed, so it is still taken."""
    season, first, _second, guest = _two_nights_of_one_guest(client)
    charged = _nights(client, season, guest)[first][0]
    membership = db_session.get(
        ClubMemberRow, {"club_id": season["club_id"], "player_id": guest}
    )
    assert membership is not None
    db_session.delete(membership)
    db_session.commit()

    paid = _pay(client, season, guest, charged, first)

    assert paid.status_code == 200, paid.text


def test_somebody_with_nothing_on_the_books_still_cannot_be_paid(
    client: TestClient,
) -> None:
    season = start_season(client, member_names=["Alice"], capacity=2)
    other = start_season(client, member_names=["Stranger"], capacity=2)
    stranger = next(
        m["id"] for m in client.get(f"/seasons/{other['id']}").json()["members"]
    )
    # Back to the first club's organizer.
    client.headers.update({"Authorization": f"Bearer {season['organizer_token']}"})

    paid = client.post(
        f"/clubs/{season['club_id']}/players/{stranger}/payments",
        json={"amount": "100"},
    )

    assert paid.status_code == 400
    assert paid.json()["detail"] == "Player is not a member of this club"


def test_somebody_who_still_owes_cannot_be_taken_out_of_the_club(
    client: TestClient,
) -> None:
    season, _first, _second, guest = _two_nights_of_one_guest(client)

    removed = client.delete(f"/clubs/{season['club_id']}/members/{guest}")

    assert removed.status_code == 400
    assert removed.json()["detail"].startswith("Still has money to settle")


def test_somebody_still_queued_for_a_game_to_come_cannot_be_taken_out(
    client: TestClient,
) -> None:
    # Queued, so nothing is owed — but they still hold a place in line.
    season = start_season(
        client, member_names=["Alice"], capacity=1, game_dates=[_future(7)]
    )
    queued = client.post(
        "/drop-ins",
        json={"player_name": "候補的人", "game_id": season["games"][0]["id"]},
    ).json()
    assert queued["status"] == "waitlisted"

    removed = client.delete(f"/clubs/{season['club_id']}/members/{queued['player_id']}")

    assert removed.status_code == 400
    assert removed.json()["detail"].startswith("Still signed up for a game to come")


def test_somebody_settled_and_finished_with_can_be_taken_out(
    client: TestClient,
) -> None:
    season, first, second, guest = _two_nights_of_one_guest(client)
    for game_id in (first, second):
        signups = client.get(f"/seasons/{season['id']}").json()["games"]
        drop_in = next(
            d
            for g in signups
            if g["id"] == game_id
            for d in g["confirmed_drop_ins"]
            if d["player_id"] == guest
        )
        client.post(f"/drop-ins/{drop_in['id']}/cancel", json={})

    removed = client.delete(f"/clubs/{season['club_id']}/members/{guest}")

    assert removed.status_code == 204, removed.text
