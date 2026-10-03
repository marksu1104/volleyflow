"""A season's money figures count that season and the ones before it,
never one that starts later.

A member's fee is charged the moment they join a season, so booking the
next season early puts its fee on everybody's ledger straight away.
Before `through_season` existed the settlement screen summed the whole
ledger: settling October with January already booked showed every
member owing January's fee, and 已收 recorded it as October's money.
"""

from decimal import Decimal
from typing import Any

from fastapi.testclient import TestClient

from tests.api.factories import start_season


def _two_seasons(client: TestClient) -> tuple[dict[str, Any], dict[str, Any]]:
    autumn = start_season(
        client, member_names=["Alice"], game_dates=["2031-10-07", "2031-10-14"]
    )
    winter = start_season(
        client,
        member_names=["Alice"],
        game_dates=["2032-01-06", "2032-01-13"],
        club_id=autumn["club_id"],
    )
    return autumn, winter


def _alice(client: TestClient, season: dict[str, Any]) -> dict[str, Any]:
    rows = client.get(
        f"/clubs/{season['club_id']}/balances", params={"season_id": season["id"]}
    ).json()
    alice_id = season["member_ids"][0]
    row: dict[str, Any] = next(r for r in rows if r["player_id"] == alice_id)
    return row


def test_a_season_booked_ahead_stays_out_of_the_earlier_seasons_figure(
    client: TestClient,
) -> None:
    autumn, _winter = _two_seasons(client)

    row = _alice(client, autumn)

    assert Decimal(row["through_season"]) == Decimal(row["season_fee_charged"])
    assert Decimal(row["balance"]) == 2 * Decimal(row["season_fee_charged"]), (
        "the whole ledger still has both fees; only the season figure leaves one out"
    )


def test_the_later_seasons_figure_carries_whatever_the_earlier_one_left(
    client: TestClient,
) -> None:
    _autumn, winter = _two_seasons(client)

    row = _alice(client, winter)

    assert row["through_season"] == row["balance"]


def test_seasons_are_ordered_by_their_first_game_not_by_when_they_were_created(
    client: TestClient,
) -> None:
    # Booked out of order: winter first, then the autumn before it.
    winter = start_season(
        client, member_names=["Alice"], game_dates=["2032-01-06", "2032-01-13"]
    )
    autumn = start_season(
        client,
        member_names=["Alice"],
        game_dates=["2031-10-07", "2031-10-14"],
        club_id=winter["club_id"],
    )

    row = _alice(client, autumn)

    assert Decimal(row["through_season"]) == Decimal(row["season_fee_charged"])


def test_a_payment_tied_to_no_season_counts_in_every_season(
    client: TestClient,
) -> None:
    autumn, _winter = _two_seasons(client)
    alice_id = autumn["member_ids"][0]
    client.post(
        f"/clubs/{autumn['club_id']}/players/{alice_id}/payments",
        json={"amount": "100"},
    )

    row = _alice(client, autumn)

    assert Decimal(row["through_season"]) == Decimal(row["season_fee_charged"]) + 100


def test_the_members_ledger_says_when_each_season_starts(client: TestClient) -> None:
    # The member screen leaves out later seasons itself, so it can switch
    # season without a second request; this is what it decides with.
    autumn, winter = _two_seasons(client)
    alice_id = autumn["member_ids"][0]

    ledger = client.get(f"/clubs/{autumn['club_id']}/players/{alice_id}/ledger").json()

    assert ledger["season_starts"] == {
        str(autumn["id"]): "2031-10-07",
        str(winter["id"]): "2032-01-06",
    }


def test_my_clubs_list_counts_up_to_the_season_in_play(client: TestClient) -> None:
    # 我的帳務's figure per club. Winter is booked already and has
    # charged its fee, but autumn is the season under way.
    from datetime import date, timedelta

    def days(n: int) -> str:
        return (date.today() + timedelta(days=n)).isoformat()

    autumn = start_season(
        client, member_names=["Test Organizer"], game_dates=[days(-7), days(7)]
    )
    start_season(
        client,
        member_names=["Test Organizer"],
        game_dates=[days(90)],
        club_id=autumn["club_id"],
    )
    me = autumn["member_ids"][0]
    autumn_fee = _alice(client, autumn)["season_fee_charged"]

    clubs = client.get(f"/players/{me}/clubs").json()

    assert Decimal(clubs[0]["balance"]) == Decimal(autumn_fee)
