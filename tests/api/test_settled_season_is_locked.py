"""A settled season accepts no changes at all.

`CLAUDE.md` 2.4: settlement "computes each member's absence refund and
locks the season". The roster and pricing endpoints had honoured that
from the start; attendance had not, and nothing said so — measured on
2026-09-12, after settling you could still sign somebody up, which wrote
a real charge onto closed books, and still record a member's leave,
which earned a refund that could never be paid because a season cannot
be settled twice.

The first test lists every way in, rather than one test per endpoint:
the property worth protecting is "nothing gets through", and a route
added later that forgets the check should fail *this* test rather than
quietly not have one of its own.
"""

from typing import Any

from fastapi.testclient import TestClient

from tests.api.factories import start_season


def _season_with_everything(client: TestClient) -> dict[str, Any]:
    """A season holding one of each thing that can later be changed: a
    live absence, two confirmed drop-ins filling the court, and somebody
    queued behind them.

    Capacity 3 against two members: Alice's leave frees one place and the
    two drop-ins take that and the one she'd have had, so the court is
    full and the fourth person queues.
    """
    season = start_season(
        client, member_names=["Alice", "Bob"], capacity=3, game_dates=["2026-08-18"]
    )
    game_id = season["games"][0]["id"]
    season["game_id"] = game_id
    season["absence"] = client.post(
        "/absences", json={"player_name": "Alice", "game_id": game_id}
    ).json()
    season["drop_in"] = client.post(
        "/drop-ins", json={"player_name": "訪客", "game_id": game_id}
    ).json()
    client.post("/drop-ins", json={"player_name": "第二位", "game_id": game_id})
    queued = client.post(
        "/drop-ins", json={"player_name": "排隊的", "game_id": game_id}
    ).json()
    assert queued["status"] == "waitlisted", "the setup must really produce a queue"
    season["queued"] = queued
    season["member_id"] = client.get(f"/seasons/{season['id']}").json()["members"][0][
        "id"
    ]
    return season


def test_a_settled_season_refuses_every_change(client: TestClient) -> None:
    season = _season_with_everything(client)
    game_id = season["game_id"]
    season_id = season["id"]
    client.post(f"/seasons/{season_id}/settle", json={})

    attempts = {
        "record an absence": client.post(
            "/absences", json={"player_name": "Bob", "game_id": game_id}
        ),
        "cancel an absence": client.post(
            f"/absences/{season['absence']['id']}/cancel", json={}
        ),
        "sign somebody up": client.post(
            "/drop-ins", json={"player_name": "遲到的", "game_id": game_id}
        ),
        "sign several up": client.post(
            f"/games/{game_id}/drop-ins",
            json={"people": [{"player_name": "一群人", "gender": "male"}]},
        ),
        "cancel a signup": client.post(
            f"/drop-ins/{season['drop_in']['id']}/cancel", json={}
        ),
        "name a substitute": client.put(
            f"/absences/{season['absence']['id']}/substitute",
            json={"player_name": "代打", "gender": "male"},
        ),
        "leave the queue": client.post(
            f"/waitlist/{season['queued']['id']}/cancel", json={}
        ),
        "promote off the queue": client.post(
            f"/waitlist/{season['queued']['id']}/promote", json={}
        ),
        "cancel a game": client.post(
            f"/games/{game_id}/cancel", json={"refunded": True}
        ),
        "correct the air conditioning": client.put(
            f"/games/{game_id}/air-conditioning", json={"air_conditioned": True}
        ),
        "add a member": client.post(
            f"/seasons/{season_id}/members", json={"player_name": "新人"}
        ),
        "remove a member": client.delete(
            f"/seasons/{season_id}/members/{season['member_id']}"
        ),
        "re-price the season": client.patch(
            f"/seasons/{season_id}", json={"capacity": 9}
        ),
        "settle it again": client.post(f"/seasons/{season_id}/settle", json={}),
    }

    got_through = {name: r.status_code for name, r in attempts.items() if r.is_success}
    assert not got_through, f"a locked season let these through: {got_through}"


def test_the_refusal_says_why(client: TestClient) -> None:
    # The frontend turns this into Chinese by matching on the phrase —
    # see translateApiError in shared.js — so the wording is part of the
    # contract, not only a message.
    season = _season_with_everything(client)
    client.post(f"/seasons/{season['id']}/settle", json={})

    response = client.post(
        "/drop-ins", json={"player_name": "遲到的", "game_id": season["game_id"]}
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Season is already settled"


def test_signing_up_after_settlement_writes_no_money(client: TestClient) -> None:
    # The reason this matters, not just that a status code changed. The
    # charge landed on books that had already been closed and paid out
    # from, and nothing would ever account for it.
    season = _season_with_everything(client)
    client.post(f"/seasons/{season['id']}/settle", json={})

    before = client.get(
        f"/clubs/{season['club_id']}/balances?season_id={season['id']}"
    ).json()

    refused = client.post(
        "/drop-ins", json={"player_name": "遲到的", "game_id": season["game_id"]}
    )

    assert refused.status_code == 400
    after = client.get(
        f"/clubs/{season['club_id']}/balances?season_id={season['id']}"
    ).json()
    assert after == before, "a refused signup must leave the closed books untouched"


def test_an_unsettled_season_still_allows_all_of_it(client: TestClient) -> None:
    """The other half: the lock must be the settlement, not these calls
    quietly breaking. Without this, a guard that refused everything
    always would pass the test above.

    A season each, because these interact — recording Bob's leave frees a
    place, which promotes the person off the queue, so the queue id the
    next assertion wants is gone.
    """
    away = _season_with_everything(client)
    assert (
        client.post(
            "/absences", json={"player_name": "Bob", "game_id": away["game_id"]}
        ).status_code
        == 200
    )

    cancelling = _season_with_everything(client)
    assert (
        client.post(
            f"/drop-ins/{cancelling['drop_in']['id']}/cancel", json={}
        ).status_code
        == 200
    )

    queueing = _season_with_everything(client)
    assert (
        client.post(f"/waitlist/{queueing['queued']['id']}/cancel", json={}).status_code
        == 200
    )
