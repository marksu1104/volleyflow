"""Changing one game: its date, its venue, or its time.

The venue shifts a booking, the club swaps an evening, or one night runs
at a different court or hour. Everything already recorded against that
night has to come with it, and nobody's money may move — see
routes.games.update_game.

The venue and the time are display-only here. A replacement court that
costs a *different amount* is a separate question and is deliberately
not what this endpoint does; see docs/billing-rules.md.
"""

from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from fastapi.testclient import TestClient

from tests.api.factories import auth_headers, identify, join_club, start_season


def _in(days: int) -> str:
    """A date far enough ahead that the server's Taiwan "today" and the
    test runner's clock cannot disagree about it."""
    return (date.today() + timedelta(days=days)).isoformat()


def _season(client: TestClient) -> dict[str, Any]:
    return start_season(client, game_dates=[_in(30), _in(37)], member_names=["Alice"])


def test_moving_a_game_takes_everything_recorded_against_it_along(
    client: TestClient,
) -> None:
    season = _season(client)
    game = season["games"][0]
    client.post("/absences", json={"player_name": "Alice", "game_id": game["id"]})
    client.post("/drop-ins", json={"player_name": "Carol", "game_id": game["id"]})

    moved = client.patch(f"/games/{game['id']}", json={"date": _in(31)})

    assert moved.status_code == 200, moved.text
    assert moved.json()["date"] == _in(31)
    after = client.get(f"/seasons/{season['id']}").json()["games"][0]
    assert after["date"] == _in(31)
    assert [a["player_name"] for a in after["absences"]] == ["Alice"]
    assert [d["player_name"] for d in after["confirmed_drop_ins"]] == ["Carol"]


def test_moving_a_game_charges_nobody_anything(client: TestClient) -> None:
    # The season keeps the same number of games at the same share, so the
    # ledger has no reason to move — and if it ever does, it will be this
    # that catches it.
    season = start_season(
        client, game_dates=[_in(30), _in(37)], member_names=["Alice"], capacity=2
    )
    ledger_before = client.get(
        f"/clubs/{season['club_id']}/players/{season['member_ids'][0]}/ledger"
    ).json()

    client.patch(f"/games/{season['games'][0]['id']}", json={"date": _in(31)})

    ledger_after = client.get(
        f"/clubs/{season['club_id']}/players/{season['member_ids'][0]}/ledger"
    ).json()
    assert ledger_after["balance"] == ledger_before["balance"]
    assert len(ledger_after["entries"]) == len(ledger_before["entries"])


def test_two_games_may_not_share_one_evening(client: TestClient) -> None:
    season = _season(client)

    refused = client.patch(
        f"/games/{season['games'][0]['id']}", json={"date": season["games"][1]["date"]}
    )

    assert refused.status_code == 400
    assert "already has a game" in refused.json()["detail"]


def test_a_game_cannot_be_moved_into_the_past(client: TestClient) -> None:
    season = _season(client)

    refused = client.patch(f"/games/{season['games'][0]['id']}", json={"date": _in(-3)})

    assert refused.status_code == 400
    assert "been and gone" in refused.json()["detail"]


def test_a_cancelled_game_cannot_be_moved(client: TestClient) -> None:
    # A called-off night is a statement about a date. Moving it would
    # rewrite what happened rather than schedule anything.
    season = _season(client)
    game_id = season["games"][0]["id"]
    client.post(f"/games/{game_id}/cancel", json={"refunded": True})

    refused = client.patch(f"/games/{game_id}", json={"date": _in(31)})

    assert refused.status_code == 400
    assert "cancelled" in refused.json()["detail"]


def test_a_settled_season_refuses_a_move(client: TestClient) -> None:
    season = start_season(client, game_dates=[_in(30)], member_names=["Alice"])
    client.post(f"/seasons/{season['id']}/settle")

    refused = client.patch(f"/games/{season['games'][0]['id']}", json={"date": _in(31)})

    assert refused.status_code == 400
    assert "settled" in refused.json()["detail"]


def test_a_member_may_not_move_a_game(client: TestClient) -> None:
    season = _season(client)
    member = identify(client, "Mallory")
    join_club(client, season["club_id"], auth_headers(member["token"]))

    refused = client.patch(
        f"/games/{season['games'][0]['id']}",
        json={"date": _in(31)},
        headers=auth_headers(member["token"]),
    )

    assert refused.status_code == 403


def test_a_night_can_run_at_another_venue_without_moving(client: TestClient) -> None:
    season = _season(client)
    game = season["games"][0]

    changed = client.patch(
        f"/games/{game['id']}",
        json={
            "location": "第二球場",
            "start_time": "19:00:00",
            "end_time": "22:00:00",
        },
    )

    assert changed.status_code == 200, changed.text
    after = client.get(f"/seasons/{season['id']}").json()["games"][0]
    assert after["location"] == "第二球場"
    assert after["start_time"] == "19:00:00"
    assert after["end_time"] == "22:00:00"
    # No date in the request, so the night must not have moved — the
    # whole point of updating only what was actually sent.
    assert after["date"] == game["date"]


def test_clearing_a_nights_own_venue_puts_it_back_on_the_seasons(
    client: TestClient,
) -> None:
    # Null is a real instruction here, not a missing field: "back to the
    # same venue as every other night". The screen then falls back to the
    # season's own — see gameLocation in shared.js.
    season = _season(client)
    game = season["games"][0]
    client.patch(f"/games/{game['id']}", json={"location": "第二球場"})

    client.patch(f"/games/{game['id']}", json={"location": None})

    after = client.get(f"/seasons/{season['id']}").json()["games"][0]
    assert after["location"] is None


def test_a_night_can_move_and_change_venue_in_one_request(client: TestClient) -> None:
    season = _season(client)
    game = season["games"][0]

    client.patch(f"/games/{game['id']}", json={"date": _in(31), "location": "第二球場"})

    after = client.get(f"/seasons/{season['id']}").json()["games"][0]
    assert after["date"] == _in(31)
    assert after["location"] == "第二球場"


def test_changing_a_nights_venue_charges_nobody_anything(client: TestClient) -> None:
    # The venue's *name* and the time are display-only, so an edit that
    # changes nobody's math writes nothing (docs/billing-rules.md). What
    # a different court *costs* is a separate field — venue_cost_delta,
    # added 2026-09-17 — and it is the only thing on this endpoint that
    # moves money; the tests below cover that. This one is what catches
    # the display-only three quietly acquiring a price.
    season = start_season(
        client, game_dates=[_in(30), _in(37)], member_names=["Alice"], capacity=2
    )
    before = client.get(
        f"/clubs/{season['club_id']}/players/{season['member_ids'][0]}/ledger"
    ).json()

    client.patch(
        f"/games/{season['games'][0]['id']}",
        json={"location": "第二球場", "start_time": "20:00:00"},
    )

    after = client.get(
        f"/clubs/{season['club_id']}/players/{season['member_ids'][0]}/ledger"
    ).json()
    assert after["balance"] == before["balance"]
    assert len(after["entries"]) == len(before["entries"])


def _priced_season(client: TestClient) -> dict[str, Any]:
    """Two games, one member, capacity 2 — so a delta of 200 is exactly
    100 a head and the arithmetic below needs no rounding."""
    return start_season(
        client, game_dates=[_in(30), _in(37)], member_names=["Alice"], capacity=2
    )


def test_a_pricier_court_raises_that_night_only(client: TestClient) -> None:
    # The property the whole model exists for. total_venue_cost and
    # delta_total both rise by 200, so base_each does not move at all —
    # which is why the other night cannot change price, whatever the
    # season's total happens to be. See docs/billing-rules.md,
    # "A different venue for one night".
    season = _priced_season(client)
    before = client.get(f"/seasons/{season['id']}").json()

    moved = client.patch(
        f"/games/{season['games'][0]['id']}", json={"venue_cost_delta": "200"}
    )

    assert moved.status_code == 200, moved.text
    after = client.get(f"/seasons/{season['id']}").json()
    assert Decimal(str(after["total_venue_cost"])) == Decimal(
        str(before["total_venue_cost"])
    ) + Decimal("200"), "the club really does transfer more"
    assert Decimal(str(after["games"][1]["share"])) == Decimal(
        str(before["games"][1]["share"])
    ), "the other night does not move a cent"
    assert Decimal(str(after["games"][0]["share"])) == Decimal(
        str(before["games"][0]["share"])
    ) + Decimal("100"), "200 over a capacity of 2"


def test_a_pricier_court_corrects_the_charge_with_one_adjustment(
    client: TestClient,
) -> None:
    # Never an edit to what was already charged — one new entry for the
    # difference, so the ledger still says what changed and why.
    season = _priced_season(client)
    ledger_url = f"/clubs/{season['club_id']}/players/{season['member_ids'][0]}/ledger"
    before = client.get(ledger_url).json()

    client.patch(f"/games/{season['games'][0]['id']}", json={"venue_cost_delta": "200"})

    after = client.get(ledger_url).json()
    assert len(after["entries"]) == len(before["entries"]) + 1
    assert Decimal(str(after["balance"])) == Decimal(str(before["balance"])) - Decimal(
        "100"
    )


def test_clearing_the_delta_puts_the_season_back(client: TestClient) -> None:
    season = _priced_season(client)
    before = client.get(f"/seasons/{season['id']}").json()
    game_id = season["games"][0]["id"]
    client.patch(f"/games/{game_id}", json={"venue_cost_delta": "200"})

    client.patch(f"/games/{game_id}", json={"venue_cost_delta": None})

    after = client.get(f"/seasons/{season['id']}").json()
    assert Decimal(str(after["total_venue_cost"])) == Decimal(
        str(before["total_venue_cost"])
    )
    assert Decimal(str(after["games"][0]["venue_cost_delta"])) == Decimal("0")
    assert Decimal(str(after["games"][0]["share"])) == Decimal(
        str(before["games"][0]["share"])
    )


def test_a_refunded_cancellation_hands_the_extra_back_too(client: TestClient) -> None:
    # The venue returned this night's cost, the pricier court included,
    # so the delta goes with it. Leaving it behind would keep money
    # subtracted from the base for a night nobody is charged for — money
    # the club would never collect. docs/billing-rules.md, "Cancellation".
    season = _priced_season(client)
    before = client.get(f"/seasons/{season['id']}").json()
    game_id = season["games"][0]["id"]
    client.patch(f"/games/{game_id}", json={"venue_cost_delta": "200"})

    client.post(f"/games/{game_id}/cancel", json={"refunded": True})

    after = client.get(f"/seasons/{season['id']}").json()
    assert Decimal(str(after["total_venue_cost"])) == Decimal(
        str(before["total_venue_cost"])
    )
    assert Decimal(str(after["games"][0]["venue_cost_delta"])) == Decimal("0")


def test_an_unrefunded_cancellation_keeps_the_extra(client: TestClient) -> None:
    # That court was paid for whether or not anybody played on it.
    season = _priced_season(client)
    game_id = season["games"][0]["id"]
    client.patch(f"/games/{game_id}", json={"venue_cost_delta": "200"})
    with_delta = client.get(f"/seasons/{season['id']}").json()

    client.post(f"/games/{game_id}/cancel", json={"refunded": False})

    after = client.get(f"/seasons/{season['id']}").json()
    assert Decimal(str(after["total_venue_cost"])) == Decimal(
        str(with_delta["total_venue_cost"])
    )
    assert Decimal(str(after["games"][0]["venue_cost_delta"])) == Decimal("200")


def test_a_delta_that_would_make_the_season_cost_negative_is_refused(
    client: TestClient,
) -> None:
    season = _priced_season(client)
    total = client.get(f"/seasons/{season['id']}").json()["total_venue_cost"]

    refused = client.patch(
        f"/games/{season['games'][0]['id']}",
        json={"venue_cost_delta": str(-(Decimal(str(total)) + Decimal("1")))},
    )

    assert refused.status_code == 400
    assert "negative" in refused.json()["detail"]


def test_a_member_may_not_reprice_a_night(client: TestClient) -> None:
    season = _priced_season(client)
    member = identify(client, "Mallory")
    join_club(client, season["club_id"], auth_headers(member["token"]))

    refused = client.patch(
        f"/games/{season['games'][0]['id']}",
        json={"venue_cost_delta": "200"},
        headers=auth_headers(member["token"]),
    )

    assert refused.status_code == 403
