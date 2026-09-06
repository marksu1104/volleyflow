"""Tests for the API routes."""

from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.api.factories import start_season as _start_season
from volleyflow.api.routes import _today_in_taiwan
from volleyflow.db.models import AbsenceRow, DropInRow, PlayerRow


def test_list_seasons_summarizes_each_season(client: TestClient) -> None:
    _start_season(
        client, game_dates=["2026-08-18", "2026-08-25"], member_names=["Alice"]
    )

    response = client.get("/seasons")

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["first_game_date"] == "2026-08-18"
    assert body[0]["last_game_date"] == "2026-08-25"
    assert body[0]["total_games"] == 2
    assert body[0]["member_count"] == 1
    assert body[0]["settled"] is False


def test_list_seasons_reflects_settled_status(client: TestClient) -> None:
    season = _start_season(client, member_names=["Alice"])
    client.post(f"/seasons/{season['id']}/settle")

    body = client.get("/seasons").json()

    assert body[0]["settled"] is True


def test_list_seasons_is_empty_with_no_seasons(client: TestClient) -> None:
    response = client.get("/seasons")

    assert response.json() == []


def test_start_season_creates_games_and_members(client: TestClient) -> None:
    body = _start_season(client)

    assert body["total_venue_cost"] == "10000"
    assert len(body["games"]) == 2
    assert len(body["member_ids"]) == 2


def test_start_season_reuses_an_existing_player_by_name(client: TestClient) -> None:
    first = _start_season(client, member_names=["Alice"])
    second = _start_season(client, member_names=["Alice"])

    assert first["member_ids"] == second["member_ids"]


def test_start_season_rejects_an_empty_game_list(client: TestClient) -> None:
    response = client.post(
        "/seasons",
        json={
            "total_venue_cost": "10000",
            "game_dates": [],
            "member_names": ["Alice"],
        },
    )

    assert response.status_code == 422


def test_record_absence_for_a_member_succeeds(client: TestClient) -> None:
    season = _start_season(client)
    game_id = season["games"][0]["id"]

    response = client.post(
        "/absences", json={"player_name": "Alice", "game_id": game_id}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["player_id"] == season["member_ids"][0]
    assert body["game_id"] == game_id


def test_record_absence_for_an_unknown_game_returns_404(client: TestClient) -> None:
    _start_season(client)

    response = client.post(
        "/absences", json={"player_name": "Alice", "game_id": 999_999}
    )

    assert response.status_code == 404


def test_record_absence_for_an_unknown_player_returns_404(client: TestClient) -> None:
    season = _start_season(client)
    game_id = season["games"][0]["id"]

    response = client.post(
        "/absences", json={"player_name": "Nobody", "game_id": game_id}
    )

    assert response.status_code == 404


def test_record_absence_for_a_non_member_returns_400(client: TestClient) -> None:
    season = _start_season(client, member_names=["Alice"])
    game_id = season["games"][0]["id"]
    _start_season(client, member_names=["Carol"])  # Carol exists, wrong season

    response = client.post(
        "/absences", json={"player_name": "Carol", "game_id": game_id}
    )

    assert response.status_code == 400


def test_sign_up_confirms_when_there_is_room(client: TestClient) -> None:
    season = _start_season(client, member_names=["Alice"], capacity=18)
    game_id = season["games"][0]["id"]

    response = client.post(
        "/drop-ins", json={"player_name": "Carol", "game_id": game_id}
    )

    assert response.status_code == 200
    assert response.json()["status"] == "confirmed"


def test_sign_up_waitlists_once_the_game_is_full(client: TestClient) -> None:
    # capacity=1 and one member fills it before anyone else can join
    season = _start_season(client, member_names=["Alice"], capacity=1)
    game_id = season["games"][0]["id"]

    response = client.post(
        "/drop-ins", json={"player_name": "Carol", "game_id": game_id}
    )

    assert response.status_code == 200
    assert response.json()["status"] == "waitlisted"


def test_absence_promotes_the_earliest_waitlisted_player(client: TestClient) -> None:
    season = _start_season(client, member_names=["Alice"], capacity=1)
    game_id = season["games"][0]["id"]
    waitlisted = client.post(
        "/drop-ins", json={"player_name": "Carol", "game_id": game_id}
    )
    assert waitlisted.json()["status"] == "waitlisted"

    response = client.post(
        "/absences", json={"player_name": "Alice", "game_id": game_id}
    )

    assert response.json()["promoted_from_waitlist"] == waitlisted.json()["player_id"]


def test_absence_with_nobody_waiting_promotes_nobody(client: TestClient) -> None:
    season = _start_season(client, member_names=["Alice"], capacity=18)
    game_id = season["games"][0]["id"]

    response = client.post(
        "/absences", json={"player_name": "Alice", "game_id": game_id}
    )

    assert response.json()["promoted_from_waitlist"] is None


def test_cancel_marks_the_drop_in_cancelled(client: TestClient) -> None:
    season = _start_season(client, member_names=["Alice"], capacity=18)
    game_id = season["games"][0]["id"]
    signup = client.post("/drop-ins", json={"player_name": "Carol", "game_id": game_id})
    drop_in_id = signup.json()["id"]

    response = client.post(f"/drop-ins/{drop_in_id}/cancel")

    assert response.status_code == 200
    assert response.json()["cancelled_at"] is not None


def test_cancel_promotes_the_earliest_waitlisted_player(client: TestClient) -> None:
    # 1 member + capacity 2 leaves exactly one open drop-in slot.
    season = _start_season(client, member_names=["Alice"], capacity=2)
    game_id = season["games"][0]["id"]
    confirmed = client.post(
        "/drop-ins", json={"player_name": "Bob", "game_id": game_id}
    )
    waitlisted = client.post(
        "/drop-ins", json={"player_name": "Carol", "game_id": game_id}
    )
    assert confirmed.json()["status"] == "confirmed"
    assert waitlisted.json()["status"] == "waitlisted"

    response = client.post(f"/drop-ins/{confirmed.json()['id']}/cancel")

    assert response.json()["promoted_from_waitlist"] == waitlisted.json()["player_id"]


def test_cancel_rejects_an_already_cancelled_drop_in(client: TestClient) -> None:
    season = _start_season(client, member_names=["Alice"], capacity=18)
    game_id = season["games"][0]["id"]
    signup = client.post("/drop-ins", json={"player_name": "Carol", "game_id": game_id})
    drop_in_id = signup.json()["id"]
    client.post(f"/drop-ins/{drop_in_id}/cancel")

    response = client.post(f"/drop-ins/{drop_in_id}/cancel")

    assert response.status_code == 400


def test_cancel_an_unknown_drop_in_returns_404(client: TestClient) -> None:
    response = client.post("/drop-ins/999999/cancel")

    assert response.status_code == 404


def _clean_season(client: TestClient) -> dict[str, Any]:
    """total_venue_cost=10000, 8 games, 5 members -> share_per_game = 250."""
    return _start_season(
        client,
        total_venue_cost="10000",
        game_dates=[f"2026-08-{18 + i:02d}" for i in range(8)],
        member_names=["Alice", "Bob", "Carol", "Dave", "Eve"],
    )


def test_settlement_baseline_charges_full_season_fee(client: TestClient) -> None:
    season = _clean_season(client)

    response = client.get(f"/seasons/{season['id']}/settlement")

    assert response.status_code == 200
    body = response.json()
    assert len(body["members"]) == 5
    for member in body["members"]:
        assert member["season_fee"] == "2000"
        assert member["refund"] == "0"
        assert member["net"] == "-2000"


def test_settlement_refunds_a_covered_absence(client: TestClient) -> None:
    season = _clean_season(client)
    game_id = season["games"][0]["id"]
    client.post("/absences", json={"player_name": "Alice", "game_id": game_id})
    client.post("/drop-ins", json={"player_name": "Frank", "game_id": game_id})

    response = client.get(f"/seasons/{season['id']}/settlement")

    body = response.json()
    alice = next(m for m in body["members"] if m["player_name"] == "Alice")
    assert alice["refund"] == "250"
    assert alice["net"] == "-1750"


def test_settlement_for_an_unknown_season_returns_404(client: TestClient) -> None:
    response = client.get("/seasons/999999/settlement")

    assert response.status_code == 404


def test_get_season_lists_games_and_members(client: TestClient) -> None:
    season = _start_season(client, member_names=["Alice", "Bob"])

    response = client.get(f"/seasons/{season['id']}")

    assert response.status_code == 200
    body = response.json()
    assert {m["name"] for m in body["members"]} == {"Alice", "Bob"}
    assert len(body["games"]) == 2
    assert body["games"][0]["absences"] == []
    assert body["games"][0]["confirmed_drop_ins"] == []
    assert body["games"][0]["waitlist_entries"] == []


def test_get_season_reflects_absences_signups_and_waitlist(
    client: TestClient,
) -> None:
    season = _start_season(client, member_names=["Alice"], capacity=1)
    game_id = season["games"][0]["id"]
    alice_absence = client.post(
        "/absences", json={"player_name": "Alice", "game_id": game_id}
    ).json()
    bob_signup = client.post(
        "/drop-ins", json={"player_name": "Bob", "game_id": game_id}
    )
    carol_signup = client.post(
        "/drop-ins", json={"player_name": "Carol", "game_id": game_id}
    )

    response = client.get(f"/seasons/{season['id']}")

    game = next(g for g in response.json()["games"] if g["id"] == game_id)
    assert game["absences"] == [
        {"id": alice_absence["id"], "player_name": "Alice", "covered_by": "Bob"}
    ]
    assert game["confirmed_drop_ins"] == [
        {
            "id": bob_signup.json()["id"],
            "player_name": "Bob",
            "gender": None,
            "covering": "Alice",
        }
    ]
    assert game["waitlist_entries"] == [
        {"id": carol_signup.json()["id"], "player_name": "Carol", "gender": None}
    ]


def test_get_season_for_an_unknown_season_returns_404(client: TestClient) -> None:
    response = client.get("/seasons/999999")

    assert response.status_code == 404


def test_health_check_returns_ok(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


# --- cancelling an absence -------------------------------------------------


def test_cancel_absence_succeeds_when_uncovered(client: TestClient) -> None:
    season = _start_season(client, member_names=["Alice"])
    game_id = season["games"][0]["id"]
    absence = client.post(
        "/absences", json={"player_name": "Alice", "game_id": game_id}
    ).json()

    response = client.post(f"/absences/{absence['id']}/cancel")

    assert response.status_code == 200
    assert response.json()["id"] == absence["id"]
    body = client.get(f"/seasons/{season['id']}").json()
    game = next(g for g in body["games"] if g["id"] == game_id)
    assert game["absences"] == []


def test_cancel_absence_rejects_already_cancelled(client: TestClient) -> None:
    season = _start_season(client, member_names=["Alice"])
    game_id = season["games"][0]["id"]
    absence = client.post(
        "/absences", json={"player_name": "Alice", "game_id": game_id}
    ).json()
    client.post(f"/absences/{absence['id']}/cancel")

    response = client.post(f"/absences/{absence['id']}/cancel")

    assert response.status_code == 400


def test_cancel_absence_rejects_when_covered_by_a_drop_in(client: TestClient) -> None:
    season = _start_season(client, member_names=["Alice"], capacity=1)
    game_id = season["games"][0]["id"]
    absence = client.post(
        "/absences", json={"player_name": "Alice", "game_id": game_id}
    ).json()
    client.post("/drop-ins", json={"player_name": "Bob", "game_id": game_id})

    response = client.post(f"/absences/{absence['id']}/cancel")

    assert response.status_code == 400


def test_cancel_absence_for_unknown_id_returns_404(client: TestClient) -> None:
    response = client.post("/absences/999999/cancel")

    assert response.status_code == 404


# --- 代打: a member's own named substitute ----------------------------------


def test_set_substitute_confirms_and_charges_the_fee(client: TestClient) -> None:
    season = _start_season(client, total_venue_cost="10000", member_names=["Alice"])
    game_id = season["games"][0]["id"]
    absence = client.post(
        "/absences", json={"player_name": "Alice", "game_id": game_id}
    ).json()

    response = client.put(
        f"/absences/{absence['id']}/substitute",
        json={"player_name": "Dave", "gender": "male"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "confirmed"
    ledger = client.get(f"/players/{body['player_id']}/ledger").json()
    assert ledger["balance"] == "-5000"  # 10000 / 2 games / 1 member, one game's worth


def test_set_substitute_covers_its_specific_absence_not_fifo(
    client: TestClient,
) -> None:
    """Bob's absence is recorded first — plain FIFO would refund him —
    but Alice arranged her own substitute, so her absence is the one
    settlement refunds, not Bob's.
    """
    season = _start_season(
        client, total_venue_cost="10000", member_names=["Alice", "Bob"], capacity=2
    )
    game_id = season["games"][0]["id"]
    client.post("/absences", json={"player_name": "Bob", "game_id": game_id})
    alice_absence = client.post(
        "/absences", json={"player_name": "Alice", "game_id": game_id}
    ).json()
    client.put(
        f"/absences/{alice_absence['id']}/substitute", json={"player_name": "Dave"}
    )

    settlement = client.get(f"/seasons/{season['id']}/settlement").json()
    alice = next(m for m in settlement["members"] if m["player_name"] == "Alice")
    bob = next(m for m in settlement["members"] if m["player_name"] == "Bob")

    assert alice["refund"] == "2500"
    assert bob["refund"] == "0"


def test_set_substitute_replaces_an_existing_one(client: TestClient) -> None:
    """Calling this again with a new name swaps who's covering — the
    old substitute is refunded, the new one is charged instead of
    being rejected as "already covered".
    """
    season = _start_season(client, total_venue_cost="10000", member_names=["Alice"])
    game_id = season["games"][0]["id"]
    absence = client.post(
        "/absences", json={"player_name": "Alice", "game_id": game_id}
    ).json()
    dave = client.put(
        f"/absences/{absence['id']}/substitute", json={"player_name": "Dave"}
    ).json()

    response = client.put(
        f"/absences/{absence['id']}/substitute", json={"player_name": "Eve"}
    )

    assert response.status_code == 200
    eve = response.json()
    dave_ledger = client.get(f"/players/{dave['player_id']}/ledger").json()
    eve_ledger = client.get(f"/players/{eve['player_id']}/ledger").json()
    assert dave_ledger["balance"] == "0"  # refunded once replaced
    assert eve_ledger["balance"] == "-5000"  # now charged instead
    body = client.get(f"/seasons/{season['id']}").json()
    game = next(g for g in body["games"] if g["id"] == game_id)
    assert game["absences"] == [
        {"id": absence["id"], "player_name": "Alice", "covered_by": "Eve"}
    ]


def test_set_substitute_allowed_past_the_change_deadline(
    client: TestClient, db_session: Session
) -> None:
    """Swapping who covers an absence doesn't create the understaffed
    risk the deadline protects against — a body still fills the slot
    either way — so it's allowed even once the game is locked.
    """
    today = _today_in_taiwan().isoformat()
    season = _start_season(
        client, member_names=["Alice"], game_dates=[today], change_deadline_days=1
    )
    game_id = season["games"][0]["id"]
    absence = AbsenceRow(
        player_id=season["member_ids"][0],
        game_id=game_id,
        recorded_at=datetime.now(UTC).replace(tzinfo=None),
    )
    db_session.add(absence)
    db_session.commit()
    db_session.refresh(absence)

    response = client.put(
        f"/absences/{absence.id}/substitute", json={"player_name": "Dave"}
    )

    assert response.status_code == 200


def test_set_substitute_rejects_a_cancelled_absence(client: TestClient) -> None:
    season = _start_season(client, member_names=["Alice"])
    game_id = season["games"][0]["id"]
    absence = client.post(
        "/absences", json={"player_name": "Alice", "game_id": game_id}
    ).json()
    client.post(f"/absences/{absence['id']}/cancel")

    response = client.put(
        f"/absences/{absence['id']}/substitute", json={"player_name": "Dave"}
    )

    assert response.status_code == 400


def test_set_substitute_for_unknown_absence_returns_404(client: TestClient) -> None:
    response = client.put("/absences/999999/substitute", json={"player_name": "Dave"})

    assert response.status_code == 404


def test_set_substitute_sets_gender_for_a_new_player(client: TestClient) -> None:
    season = _start_season(client, member_names=["Alice"])
    game_id = season["games"][0]["id"]
    absence = client.post(
        "/absences", json={"player_name": "Alice", "game_id": game_id}
    ).json()

    client.put(
        f"/absences/{absence['id']}/substitute",
        json={"player_name": "Dave", "gender": "male"},
    )

    body = client.get(f"/seasons/{season['id']}").json()
    game = next(g for g in body["games"] if g["id"] == game_id)
    dave = next(d for d in game["confirmed_drop_ins"] if d["player_name"] == "Dave")
    assert dave["gender"] == "male"


def test_set_substitute_does_not_overwrite_an_existing_gender(
    client: TestClient,
) -> None:
    season = _start_season(client, member_names=["Alice"])
    game_id = season["games"][0]["id"]
    first_signup = client.post(
        "/drop-ins", json={"player_name": "Dave", "game_id": game_id}
    ).json()
    client.put(f"/players/{first_signup['player_id']}/gender", json={"gender": "male"})
    client.post(f"/drop-ins/{first_signup['id']}/cancel")
    absence = client.post(
        "/absences", json={"player_name": "Alice", "game_id": game_id}
    ).json()

    client.put(
        f"/absences/{absence['id']}/substitute",
        json={"player_name": "Dave", "gender": "female"},
    )

    body = client.get(f"/seasons/{season['id']}").json()
    game = next(g for g in body["games"] if g["id"] == game_id)
    dave = next(d for d in game["confirmed_drop_ins"] if d["player_name"] == "Dave")
    assert dave["gender"] == "male"


def test_cancelling_a_substitute_uncovers_the_absence_and_refunds_it(
    client: TestClient,
) -> None:
    """A substitute is just a drop-in with `covers_absence_id` set, so
    the ordinary /drop-ins/{id}/cancel endpoint — deadline check
    included — is how "取消代打" actually removes coverage, with no
    special-cased endpoint needed for it.
    """
    season = _start_season(client, total_venue_cost="10000", member_names=["Alice"])
    game_id = season["games"][0]["id"]
    absence = client.post(
        "/absences", json={"player_name": "Alice", "game_id": game_id}
    ).json()
    dave = client.put(
        f"/absences/{absence['id']}/substitute", json={"player_name": "Dave"}
    ).json()

    response = client.post(f"/drop-ins/{dave['id']}/cancel")

    assert response.status_code == 200
    body = client.get(f"/seasons/{season['id']}").json()
    game = next(g for g in body["games"] if g["id"] == game_id)
    assert game["absences"] == [
        {"id": absence["id"], "player_name": "Alice", "covered_by": None}
    ]
    dave_ledger = client.get(f"/players/{dave['player_id']}/ledger").json()
    assert dave_ledger["balance"] == "0"


# --- player gender -----------------------------------------------------


def test_set_player_gender_updates_it(client: TestClient) -> None:
    season = _start_season(client, member_names=["Alice"])
    player_id = season["member_ids"][0]

    response = client.put(f"/players/{player_id}/gender", json={"gender": "female"})

    assert response.status_code == 200
    assert response.json()["gender"] == "female"


def test_set_player_gender_for_unknown_player_returns_404(client: TestClient) -> None:
    response = client.put("/players/999999/gender", json={"gender": "male"})

    assert response.status_code == 404


def test_set_player_gender_rejects_an_invalid_value(client: TestClient) -> None:
    season = _start_season(client, member_names=["Alice"])
    player_id = season["member_ids"][0]

    response = client.put(f"/players/{player_id}/gender", json={"gender": "other"})

    assert response.status_code == 422


# --- change deadline -----------------------------------------------------


def test_record_absence_rejected_past_the_change_deadline(client: TestClient) -> None:
    today = _today_in_taiwan().isoformat()
    season = _start_season(
        client, member_names=["Alice"], game_dates=[today], change_deadline_days=1
    )
    game_id = season["games"][0]["id"]

    response = client.post(
        "/absences", json={"player_name": "Alice", "game_id": game_id}
    )

    assert response.status_code == 400


def test_record_absence_allowed_within_the_change_deadline(client: TestClient) -> None:
    future = (_today_in_taiwan() + timedelta(days=10)).isoformat()
    season = _start_season(
        client, member_names=["Alice"], game_dates=[future], change_deadline_days=1
    )
    game_id = season["games"][0]["id"]

    response = client.post(
        "/absences", json={"player_name": "Alice", "game_id": game_id}
    )

    assert response.status_code == 200


def test_sign_up_rejected_past_the_change_deadline(client: TestClient) -> None:
    today = _today_in_taiwan().isoformat()
    season = _start_season(
        client, member_names=["Alice"], game_dates=[today], change_deadline_days=1
    )
    game_id = season["games"][0]["id"]

    response = client.post("/drop-ins", json={"player_name": "Bob", "game_id": game_id})

    assert response.status_code == 400


def test_game_detail_locked_reflects_the_change_deadline(client: TestClient) -> None:
    today = _today_in_taiwan().isoformat()
    future = (_today_in_taiwan() + timedelta(days=10)).isoformat()
    season = _start_season(
        client,
        member_names=["Alice"],
        game_dates=[today, future],
        change_deadline_days=1,
    )

    body = client.get(f"/seasons/{season['id']}").json()

    locked_by_date = {g["date"]: g["locked"] for g in body["games"]}
    assert locked_by_date[today] is True
    assert locked_by_date[future] is False


def test_cancel_absence_rejected_past_the_change_deadline(
    client: TestClient, db_session: Session
) -> None:
    today = _today_in_taiwan().isoformat()
    season = _start_season(
        client, member_names=["Alice"], game_dates=[today], change_deadline_days=1
    )
    game_id = season["games"][0]["id"]
    absence = AbsenceRow(
        player_id=season["member_ids"][0],
        game_id=game_id,
        recorded_at=datetime.now(UTC).replace(tzinfo=None),
    )
    db_session.add(absence)
    db_session.commit()
    db_session.refresh(absence)

    response = client.post(f"/absences/{absence.id}/cancel")

    assert response.status_code == 400


def test_cancel_drop_in_rejected_past_the_change_deadline(
    client: TestClient, db_session: Session
) -> None:
    today = _today_in_taiwan().isoformat()
    season = _start_season(
        client, member_names=["Alice"], game_dates=[today], change_deadline_days=1
    )
    game_id = season["games"][0]["id"]
    bob = PlayerRow(name="Bob")
    db_session.add(bob)
    db_session.flush()
    drop_in = DropInRow(
        player_id=bob.id,
        game_id=game_id,
        signed_up_at=datetime.now(UTC).replace(tzinfo=None),
    )
    db_session.add(drop_in)
    db_session.commit()
    db_session.refresh(drop_in)

    response = client.post(f"/drop-ins/{drop_in.id}/cancel")

    assert response.status_code == 400


# --- duplicate submission guards -------------------------------------------


def test_record_absence_twice_for_the_same_game_returns_400(
    client: TestClient,
) -> None:
    season = _start_season(client, member_names=["Alice"])
    game_id = season["games"][0]["id"]
    client.post("/absences", json={"player_name": "Alice", "game_id": game_id})

    response = client.post(
        "/absences", json={"player_name": "Alice", "game_id": game_id}
    )

    assert response.status_code == 400


def test_record_absence_again_after_cancelling_succeeds(client: TestClient) -> None:
    season = _start_season(client, member_names=["Alice"])
    game_id = season["games"][0]["id"]
    first = client.post(
        "/absences", json={"player_name": "Alice", "game_id": game_id}
    ).json()
    client.post(f"/absences/{first['id']}/cancel")

    response = client.post(
        "/absences", json={"player_name": "Alice", "game_id": game_id}
    )

    assert response.status_code == 200


def test_sign_up_twice_for_the_same_game_returns_400(client: TestClient) -> None:
    season = _start_season(client, member_names=["Alice"], capacity=18)
    game_id = season["games"][0]["id"]
    client.post("/drop-ins", json={"player_name": "Carol", "game_id": game_id})

    response = client.post(
        "/drop-ins", json={"player_name": "Carol", "game_id": game_id}
    )

    assert response.status_code == 400


def test_sign_up_while_already_waitlisted_returns_400(client: TestClient) -> None:
    season = _start_season(client, member_names=["Alice"], capacity=1)
    game_id = season["games"][0]["id"]
    client.post("/drop-ins", json={"player_name": "Carol", "game_id": game_id})

    response = client.post(
        "/drop-ins", json={"player_name": "Carol", "game_id": game_id}
    )

    assert response.status_code == 400


def test_set_substitute_reassigning_the_same_player_succeeds(
    client: TestClient,
) -> None:
    """Regression test for the SQLAlchemy flush-ordering bug: without an
    explicit flush between cancelling the old substitute row and adding
    the new one, the unit of work would insert the new row before the
    old one is marked cancelled, tripping the active-substitute unique
    index when both rows are for the same player and game.
    """
    season = _start_season(client, member_names=["Alice"])
    game_id = season["games"][0]["id"]
    absence = client.post(
        "/absences", json={"player_name": "Alice", "game_id": game_id}
    ).json()
    client.put(f"/absences/{absence['id']}/substitute", json={"player_name": "Dave"})

    response = client.put(
        f"/absences/{absence['id']}/substitute", json={"player_name": "Dave"}
    )

    assert response.status_code == 200


def test_set_substitute_rejects_someone_already_signed_up(
    client: TestClient,
) -> None:
    season = _start_season(client, member_names=["Alice"], capacity=18)
    game_id = season["games"][0]["id"]
    client.post("/drop-ins", json={"player_name": "Dave", "game_id": game_id})
    absence = client.post(
        "/absences", json={"player_name": "Alice", "game_id": game_id}
    ).json()

    response = client.put(
        f"/absences/{absence['id']}/substitute", json={"player_name": "Dave"}
    )

    assert response.status_code == 400


# --- editing season settings -----------------------------------------------


def test_update_season_changes_only_the_given_fields(client: TestClient) -> None:
    season = _start_season(
        client, total_venue_cost="10000", member_names=["Alice"], capacity=18
    )

    response = client.patch(f"/seasons/{season['id']}", json={"capacity": 20})

    assert response.status_code == 200
    body = response.json()
    assert body["capacity"] == 20
    assert body["total_venue_cost"] == "10000"  # untouched


def test_update_season_can_clear_a_nullable_field_back_to_null(
    client: TestClient,
) -> None:
    season = _start_season(client, member_names=["Alice"], location="啪排郎")

    response = client.patch(f"/seasons/{season['id']}", json={"location": None})

    assert response.status_code == 200
    assert response.json()["location"] is None


def test_update_season_rejects_venue_cost_change_once_settled(
    client: TestClient,
) -> None:
    season = _start_season(client, member_names=["Alice"])
    client.post(f"/seasons/{season['id']}/settle")

    response = client.patch(
        f"/seasons/{season['id']}", json={"total_venue_cost": "20000"}
    )

    assert response.status_code == 400


def test_update_season_allows_non_cost_changes_once_settled(
    client: TestClient,
) -> None:
    season = _start_season(client, member_names=["Alice"])
    client.post(f"/seasons/{season['id']}/settle")

    response = client.patch(f"/seasons/{season['id']}", json={"location": "新場地"})

    assert response.status_code == 200
    assert response.json()["location"] == "新場地"


def test_update_season_for_an_unknown_season_returns_404(client: TestClient) -> None:
    response = client.patch("/seasons/999999", json={"capacity": 20})

    assert response.status_code == 404


# --- member management -------------------------------------------------


def test_add_member_joins_the_season(client: TestClient) -> None:
    season = _start_season(client, member_names=["Alice"])

    response = client.post(
        f"/seasons/{season['id']}/members", json={"player_name": "Bob"}
    )

    assert response.status_code == 200
    assert response.json()["name"] == "Bob"
    body = client.get(f"/seasons/{season['id']}").json()
    assert {m["name"] for m in body["members"]} == {"Alice", "Bob"}


def test_add_member_reuses_an_existing_player_by_name(client: TestClient) -> None:
    first_season = _start_season(client, member_names=["Alice"])
    second_season = _start_season(client, member_names=["Carol"])

    response = client.post(
        f"/seasons/{second_season['id']}/members", json={"player_name": "Alice"}
    )

    assert response.json()["id"] == first_season["member_ids"][0]


def test_add_member_rejects_a_duplicate(client: TestClient) -> None:
    season = _start_season(client, member_names=["Alice"])

    response = client.post(
        f"/seasons/{season['id']}/members", json={"player_name": "Alice"}
    )

    assert response.status_code == 400


def test_add_member_rejects_once_settled(client: TestClient) -> None:
    season = _start_season(client, member_names=["Alice"])
    client.post(f"/seasons/{season['id']}/settle")

    response = client.post(
        f"/seasons/{season['id']}/members", json={"player_name": "Bob"}
    )

    assert response.status_code == 400


def test_add_member_for_an_unknown_season_returns_404(client: TestClient) -> None:
    response = client.post("/seasons/999999/members", json={"player_name": "Bob"})

    assert response.status_code == 404


def test_remove_member_leaves_the_season(client: TestClient) -> None:
    season = _start_season(client, member_names=["Alice", "Bob"])
    bob_id = next(
        m["id"]
        for m in client.get(f"/seasons/{season['id']}").json()["members"]
        if m["name"] == "Bob"
    )

    response = client.delete(f"/seasons/{season['id']}/members/{bob_id}")

    assert response.status_code == 204
    body = client.get(f"/seasons/{season['id']}").json()
    assert {m["name"] for m in body["members"]} == {"Alice"}


def test_remove_member_rejects_once_settled(client: TestClient) -> None:
    season = _start_season(client, member_names=["Alice", "Bob"])
    bob_id = next(
        m["id"]
        for m in client.get(f"/seasons/{season['id']}").json()["members"]
        if m["name"] == "Bob"
    )
    client.post(f"/seasons/{season['id']}/settle")

    response = client.delete(f"/seasons/{season['id']}/members/{bob_id}")

    assert response.status_code == 400


def test_remove_member_not_in_the_season_returns_404(client: TestClient) -> None:
    season = _start_season(client, member_names=["Alice"])
    other_season = _start_season(client, member_names=["Carol"])
    carol_id = other_season["member_ids"][0]

    response = client.delete(f"/seasons/{season['id']}/members/{carol_id}")

    assert response.status_code == 404


def test_remove_member_for_an_unknown_season_returns_404(client: TestClient) -> None:
    response = client.delete("/seasons/999999/members/1")

    assert response.status_code == 404
