"""Tests for the API routes."""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.api.factories import auth_headers, create_club, identify
from tests.api.factories import start_season as _start_season
from volleyflow.api import routes
from volleyflow.api.routes import _today_in_taiwan
from volleyflow.db.models import AbsenceRow, DropInRow, PlayerRow


def test_list_seasons_summarizes_each_season(client: TestClient) -> None:
    season = _start_season(
        client, game_dates=["2026-08-18", "2026-08-25"], member_names=["Alice"]
    )

    response = client.get(f"/clubs/{season['club_id']}/seasons")

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

    body = client.get(f"/clubs/{season['club_id']}/seasons").json()

    assert body[0]["settled"] is True


def test_list_seasons_is_empty_with_no_seasons(client: TestClient) -> None:
    club = create_club(client)

    response = client.get(f"/clubs/{club['id']}/seasons")

    assert response.json() == []


def test_list_seasons_only_shows_this_clubs_seasons(client: TestClient) -> None:
    club_a_season = _start_season(client, member_names=["Alice"])
    club_b = create_club(client)
    _start_season(client, member_names=["Alice"], club_id=club_b["id"])

    # create_club above re-pointed the client at club B's organizer, who
    # can't read club A any more — ask as club A's own organizer.
    body = client.get(
        f"/clubs/{club_a_season['club_id']}/seasons",
        headers=auth_headers(club_a_season["organizer_token"]),
    ).json()

    assert [s["id"] for s in body] == [club_a_season["id"]]


def test_start_season_creates_games_and_members(client: TestClient) -> None:
    body = _start_season(client)

    assert body["total_venue_cost"] == "10000"
    assert len(body["games"]) == 2
    assert len(body["member_ids"]) == 2


def test_start_season_reuses_an_existing_player_by_name(client: TestClient) -> None:
    club = create_club(client)
    first = _start_season(client, member_names=["Alice"], club_id=club["id"])
    second = _start_season(client, member_names=["Alice"], club_id=club["id"])

    assert first["member_ids"] == second["member_ids"]


def test_start_season_treats_the_same_name_in_different_clubs_as_different_people(
    client: TestClient,
) -> None:
    first = _start_season(client, member_names=["Alice"])
    second = _start_season(client, member_names=["Alice"])

    assert first["member_ids"] != second["member_ids"]


def test_start_season_rejects_an_empty_game_list(client: TestClient) -> None:
    club = create_club(client)

    response = client.post(
        f"/clubs/{club['id']}/seasons",
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
    club = create_club(client)
    season = _start_season(client, member_names=["Alice"], club_id=club["id"])
    game_id = season["games"][0]["id"]
    # Carol is in the same club (via a different season), but not this one.
    _start_season(client, member_names=["Carol"], club_id=club["id"])

    response = client.post(
        "/absences", json={"player_name": "Carol", "game_id": game_id}
    )

    assert response.status_code == 400


def test_record_absence_for_a_player_in_a_different_club_returns_404(
    client: TestClient,
) -> None:
    season = _start_season(client, member_names=["Alice"])
    game_id = season["games"][0]["id"]
    _start_season(client, member_names=["Carol"])  # a different club entirely

    response = client.post(
        "/absences", json={"player_name": "Carol", "game_id": game_id}
    )

    assert response.status_code == 404


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
    create_club(client)

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
    create_club(client)

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
            "player_id": bob_signup.json()["player_id"],
            "player_name": "Bob",
            "gender": None,
            "covering": "Alice",
        }
    ]
    assert game["waitlist_entries"] == [
        {"id": carol_signup.json()["id"], "player_name": "Carol", "gender": None}
    ]


def test_get_season_for_an_unknown_season_returns_404(client: TestClient) -> None:
    create_club(client)  # an identified caller, so this isn't a 401

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
    create_club(client)

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
    ledger = client.get(
        f"/clubs/{season['club_id']}/players/{body['player_id']}/ledger"
    ).json()
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
    dave_ledger = client.get(
        f"/clubs/{season['club_id']}/players/{dave['player_id']}/ledger"
    ).json()
    eve_ledger = client.get(
        f"/clubs/{season['club_id']}/players/{eve['player_id']}/ledger"
    ).json()
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
    create_club(client)

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
    # Dave needs a real identity to set his own gender below — a
    # name-only club member (see _get_or_create_player) has no way to
    # authenticate as themselves, only the organizer could act for them.
    dave = identify(client, "Dave")
    client.post(f"/clubs/{season['club_id']}/join", headers=auth_headers(dave["token"]))
    first_signup = client.post(
        "/drop-ins",
        json={"player_name": "Dave", "game_id": game_id},
        headers=auth_headers(dave["token"]),
    ).json()
    client.put(
        f"/players/{first_signup['player_id']}/gender",
        json={"gender": "male"},
        headers=auth_headers(dave["token"]),
    )
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
    dave_ledger = client.get(
        f"/clubs/{season['club_id']}/players/{dave['player_id']}/ledger"
    ).json()
    assert dave_ledger["balance"] == "0"


# --- player gender -----------------------------------------------------


def test_set_player_gender_updates_it(client: TestClient) -> None:
    alice = identify(client, "Alice")

    response = client.put(
        f"/players/{alice['id']}/gender",
        json={"gender": "female"},
        headers=auth_headers(alice["token"]),
    )

    assert response.status_code == 200
    assert response.json()["gender"] == "female"


def test_set_player_gender_for_someone_else_returns_403(client: TestClient) -> None:
    alice = identify(client, "Alice")
    bob = identify(client, "Bob")

    response = client.put(
        f"/players/{alice['id']}/gender",
        json={"gender": "female"},
        headers=auth_headers(bob["token"]),
    )

    assert response.status_code == 403


def test_set_player_gender_for_unknown_player_returns_404(client: TestClient) -> None:
    alice = identify(client, "Alice")

    response = client.put(
        "/players/999999/gender",
        json={"gender": "male"},
        headers=auth_headers(alice["token"]),
    )

    assert response.status_code == 404


def test_set_player_gender_rejects_an_invalid_value(client: TestClient) -> None:
    alice = identify(client, "Alice")

    response = client.put(
        f"/players/{alice['id']}/gender",
        json={"gender": "other"},
        headers=auth_headers(alice["token"]),
    )

    assert response.status_code == 422


# --- player display name ------------------------------------------------


def test_set_player_name_updates_it(client: TestClient) -> None:
    alice = identify(client, "Alice")

    response = client.put(
        f"/players/{alice['id']}/name",
        json={"name": "阿慬"},
        headers=auth_headers(alice["token"]),
    )

    assert response.status_code == 200
    assert response.json()["name"] == "阿慬"


def test_set_player_name_disambiguates_a_collision(client: TestClient) -> None:
    identify(client, "Bob")
    alice = identify(client, "Alice")

    response = client.put(
        f"/players/{alice['id']}/name",
        json={"name": "Bob"},
        headers=auth_headers(alice["token"]),
    )

    assert response.status_code == 200
    assert response.json()["name"] == "Bob (2)"


def test_set_player_name_keeping_your_own_current_name_is_allowed(
    client: TestClient,
) -> None:
    alice = identify(client, "Alice")

    response = client.put(
        f"/players/{alice['id']}/name",
        json={"name": "Alice"},
        headers=auth_headers(alice["token"]),
    )

    assert response.status_code == 200
    assert response.json()["name"] == "Alice"


def test_set_player_name_rejects_blank(client: TestClient) -> None:
    alice = identify(client, "Alice")

    response = client.put(
        f"/players/{alice['id']}/name",
        json={"name": "   "},
        headers=auth_headers(alice["token"]),
    )

    assert response.status_code == 400


def test_set_player_name_for_someone_else_returns_403(client: TestClient) -> None:
    alice = identify(client, "Alice")
    bob = identify(client, "Bob")

    response = client.put(
        f"/players/{alice['id']}/name",
        json={"name": "New Name"},
        headers=auth_headers(bob["token"]),
    )

    assert response.status_code == 403


def test_set_player_name_for_unknown_player_returns_404(client: TestClient) -> None:
    alice = identify(client, "Alice")

    response = client.put(
        "/players/999999/name",
        json={"name": "New Name"},
        headers=auth_headers(alice["token"]),
    )

    assert response.status_code == 404


# --- LINE identity binding -------------------------------------------------


def test_identify_creates_a_new_player_for_an_unknown_line_user_id(
    client: TestClient,
) -> None:
    response = client.post(
        "/players/identify",
        json={
            "id_token": "U1",
            "display_name": "Carol",
            "picture_url": "https://example.com/carol.jpg",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "Carol"
    assert body["avatar_url"] == "https://example.com/carol.jpg"


def test_identify_returns_the_same_player_on_a_second_call(
    client: TestClient,
) -> None:
    first = client.post(
        "/players/identify",
        json={"id_token": "U1", "display_name": "Carol"},
    ).json()

    second = client.post(
        "/players/identify",
        json={"id_token": "U1", "display_name": "Carol"},
    ).json()

    assert second["id"] == first["id"]


def test_identify_never_auto_claims_an_existing_name_only_player(
    client: TestClient,
) -> None:
    """Alice was entered by name only (e.g. from a screenshot) before
    she ever opened the LIFF. Her first identify call must not silently
    guess that she's the same person and hand over that row's history —
    it should create a distinct, disambiguated Player instead, leaving
    reconciliation to the organizer.
    """
    season = _start_season(client, member_names=["Alice"])
    alice_id = season["member_ids"][0]

    response = client.post(
        "/players/identify",
        json={"id_token": "U1", "display_name": "Alice"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["id"] != alice_id
    assert body["name"] == "Alice (2)"


def test_identify_does_not_reclaim_an_already_claimed_player(
    client: TestClient,
) -> None:
    """A different real person can happen to share a LINE display name
    with someone already bound — this must not silently hand them
    someone else's identity and ledger history, and each collision gets
    its own distinct disambiguated name.
    """
    season = _start_season(client, member_names=["Alice"])
    alice_id = season["member_ids"][0]
    first = client.post(
        "/players/identify", json={"id_token": "U1", "display_name": "Alice"}
    ).json()

    response = client.post(
        "/players/identify", json={"id_token": "U2", "display_name": "Alice"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["id"] not in (alice_id, first["id"])
    assert first["name"] == "Alice (2)"
    assert body["name"] == "Alice (3)"


def test_identify_disambiguates_a_name_collision_on_create(
    client: TestClient,
) -> None:
    first = client.post(
        "/players/identify", json={"id_token": "U1", "display_name": "Bob"}
    ).json()

    second = client.post(
        "/players/identify", json={"id_token": "U2", "display_name": "Bob"}
    ).json()

    assert first["name"] == "Bob"
    assert second["name"] == "Bob (2)"
    assert second["id"] != first["id"]


def test_identify_syncs_display_name_and_avatar_on_return_visit(
    client: TestClient,
) -> None:
    first = client.post(
        "/players/identify",
        json={"id_token": "U1", "display_name": "Carol", "picture_url": "old.jpg"},
    ).json()

    response = client.post(
        "/players/identify",
        json={
            "id_token": "U1",
            "display_name": "Caroline",
            "picture_url": "new.jpg",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == first["id"]
    assert body["name"] == "Caroline"
    assert body["avatar_url"] == "new.jpg"


def test_join_pool_lists_club_members_not_on_the_season_roster(
    client: TestClient,
) -> None:
    season = _start_season(client, member_names=["Alice"])
    carol = identify(client, "Carol")
    client.post(
        f"/clubs/{season['club_id']}/join", headers=auth_headers(carol["token"])
    )

    response = client.get(f"/seasons/{season['id']}/join-pool")

    # The club's organizer is a club member too (see create_club), and
    # never automatically a season member — so the pool always has at
    # least them. Check membership, not an exact list, everywhere below.
    assert response.status_code == 200
    names = [p["name"] for p in response.json()]
    assert "Carol" in names


def test_join_pool_includes_a_drop_in_signed_up_by_name(client: TestClient) -> None:
    """Signing up by name makes someone a club member too (see
    _get_or_create_player) — a past drop-in is just as valid a
    promotion candidate as someone who joined through the LINE link.
    """
    season = _start_season(client, member_names=["Alice"], capacity=18)
    game_id = season["games"][0]["id"]
    client.post("/drop-ins", json={"player_name": "Dave", "game_id": game_id})

    response = client.get(f"/seasons/{season['id']}/join-pool")

    names = [p["name"] for p in response.json()]
    assert "Dave" in names


def test_join_pool_excludes_players_already_promoted(client: TestClient) -> None:
    season = _start_season(client, member_names=["Alice"])
    carol = identify(client, "Carol")
    client.post(
        f"/clubs/{season['club_id']}/join", headers=auth_headers(carol["token"])
    )
    client.post(f"/seasons/{season['id']}/members", json={"player_name": "Carol"})

    response = client.get(f"/seasons/{season['id']}/join-pool")

    names = [p["name"] for p in response.json()]
    assert "Carol" not in names


def test_join_pool_only_shows_this_clubs_members(client: TestClient) -> None:
    season = _start_season(client, member_names=["Alice"])
    _start_season(client, member_names=["Carol"])  # a different club entirely

    response = client.get(
        f"/seasons/{season['id']}/join-pool",
        headers=auth_headers(season["organizer_token"]),
    )

    names = [p["name"] for p in response.json()]
    assert "Carol" not in names


def test_join_pool_for_unknown_season_returns_404(client: TestClient) -> None:
    create_club(client)

    response = client.get("/seasons/999999/join-pool")

    assert response.status_code == 404


# --- clubs ------------------------------------------------------------------


def test_create_club_makes_the_creator_its_organizer(client: TestClient) -> None:
    client.post("/players/identify", json={"id_token": "U1", "display_name": "Alice"})

    response = client.post(
        "/clubs", json={"name": "Tuesday Volleyball"}, headers=auth_headers("U1")
    )

    assert response.status_code == 200
    assert response.json()["name"] == "Tuesday Volleyball"
    members = client.get(
        f"/clubs/{response.json()['id']}/members", headers=auth_headers("U1")
    ).json()
    assert members[0]["name"] == "Alice"
    assert members[0]["role"] == "organizer"


def test_create_club_without_identifying_first_returns_404(client: TestClient) -> None:
    """No /players/identify call ever happened for this token, so there's
    no Player row for get_current_player to resolve to.
    """
    response = client.post(
        "/clubs", json={"name": "A Club"}, headers=auth_headers("never-identified")
    )

    assert response.status_code == 404


def test_create_club_without_a_token_returns_401(client: TestClient) -> None:
    response = client.post("/clubs", json={"name": "A Club"})

    assert response.status_code == 401


def test_list_clubs_returns_all_clubs(client: TestClient) -> None:
    club = create_club(client, "Tuesday Volleyball")

    response = client.get("/clubs")

    assert response.status_code == 200
    assert {"id": club["id"], "name": "Tuesday Volleyball"} in response.json()


def test_list_club_members_shows_roles(client: TestClient) -> None:
    club = create_club(client)
    carol = identify(client, "Carol")
    client.post(f"/clubs/{club['id']}/join", headers=auth_headers(carol["token"]))

    response = client.get(f"/clubs/{club['id']}/members")

    assert response.status_code == 200
    roles = {m["name"]: m["role"] for m in response.json()}
    assert roles["Test Organizer"] == "organizer"
    assert roles["Carol"] == "member"


def test_list_club_members_for_unknown_club_returns_404(client: TestClient) -> None:
    create_club(client)  # an identified caller, so this isn't a 401

    response = client.get("/clubs/999999/members")

    assert response.status_code == 404


def test_join_club_adds_a_member(client: TestClient) -> None:
    club = create_club(client)
    carol = identify(client, "Carol")

    response = client.post(
        f"/clubs/{club['id']}/join", headers=auth_headers(carol["token"])
    )

    assert response.status_code == 200
    assert response.json()["name"] == "Carol"


def test_join_club_rejects_a_duplicate(client: TestClient) -> None:
    club = create_club(client)
    carol = identify(client, "Carol")
    client.post(f"/clubs/{club['id']}/join", headers=auth_headers(carol["token"]))

    response = client.post(
        f"/clubs/{club['id']}/join", headers=auth_headers(carol["token"])
    )

    assert response.status_code == 400


def test_join_unknown_club_returns_404(client: TestClient) -> None:
    carol = identify(client, "Carol")

    response = client.post("/clubs/999999/join", headers=auth_headers(carol["token"]))

    assert response.status_code == 404


# --- change deadline -----------------------------------------------------


def _member_with_login(client: TestClient, season: dict, name: str) -> dict:
    """Gives an existing roster entry a LINE identity, so a test can act
    as that member rather than as the club's organizer."""
    player = identify(client, name + " (LINE)")
    client.post(
        f"/clubs/{season['club_id']}/join", headers=auth_headers(player["token"])
    )
    roster_id = next(
        m["id"]
        for m in client.get(f"/seasons/{season['id']}").json()["members"]
        if m["name"] == name
    )
    client.post(
        f"/clubs/{season['club_id']}/players/{roster_id}/link",
        json={"line_player_id": player["id"]},
    )
    return player


def test_record_absence_rejected_past_the_change_deadline(client: TestClient) -> None:
    """For an ordinary member. The organizer is exempt — see below."""
    today = _today_in_taiwan().isoformat()
    season = _start_season(
        client, member_names=["Alice"], game_dates=[today], change_deadline_days=1
    )
    game_id = season["games"][0]["id"]
    alice = _member_with_login(client, season, "Alice")

    response = client.post(
        "/absences",
        json={"player_name": "Alice", "game_id": game_id},
        headers=auth_headers(alice["token"]),
    )

    assert response.status_code == 400


def test_the_organizer_is_not_bound_by_the_change_deadline(client: TestClient) -> None:
    """Last-minute reality is exactly what the organizer has to record:
    someone drops out an hour before, a replacement turns up. Blocking
    them doesn't keep the roster accurate, it keeps it wrong.
    """
    today = _today_in_taiwan().isoformat()
    season = _start_season(
        client, member_names=["Alice"], game_dates=[today], change_deadline_days=1
    )
    game_id = season["games"][0]["id"]

    absence = client.post(
        "/absences", json={"player_name": "Alice", "game_id": game_id}
    )
    signup = client.post("/drop-ins", json={"player_name": "Bob", "game_id": game_id})

    assert absence.status_code == 200
    assert signup.status_code == 200
    # Bob's drop-in is covering Alice's absence, and an absence someone
    # has committed to cover can't be cancelled out from under them —
    # a separate rule from the deadline, so undo the coverage first.
    assert client.post(f"/drop-ins/{signup.json()['id']}/cancel").status_code == 200
    assert client.post(f"/absences/{absence.json()['id']}/cancel").status_code == 200


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

    bob = identify(client, "Bob")
    client.post(f"/clubs/{season['club_id']}/join", headers=auth_headers(bob["token"]))

    response = client.post(
        "/drop-ins",
        json={"player_name": bob["name"], "game_id": game_id},
        headers=auth_headers(bob["token"]),
    )

    assert response.status_code == 400


def test_game_detail_locked_reflects_the_change_deadline(client: TestClient) -> None:
    """For an ordinary member. "locked" answers "can *you* still change
    this", so it depends on who's asking — see the organizer case below.
    """
    today = _today_in_taiwan().isoformat()
    future = (_today_in_taiwan() + timedelta(days=10)).isoformat()
    season = _start_season(
        client,
        member_names=["Alice"],
        game_dates=[today, future],
        change_deadline_days=1,
    )
    alice = _member_with_login(client, season, "Alice")

    body = client.get(
        f"/seasons/{season['id']}", headers=auth_headers(alice["token"])
    ).json()

    locked_by_date = {g["date"]: g["locked"] for g in body["games"]}
    assert locked_by_date[today] is True
    assert locked_by_date[future] is False


def test_nothing_is_locked_for_the_organizer(client: TestClient) -> None:
    today = _today_in_taiwan().isoformat()
    season = _start_season(
        client, member_names=["Alice"], game_dates=[today], change_deadline_days=1
    )

    body = client.get(f"/seasons/{season['id']}").json()

    assert body["games"][0]["locked"] is False


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
    alice = _member_with_login(client, season, "Alice")

    response = client.post(
        f"/absences/{absence.id}/cancel", headers=auth_headers(alice["token"])
    )

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
    bob.line_user_id = "bob-token"
    db_session.commit()

    response = client.post(
        f"/drop-ins/{drop_in.id}/cancel", headers=auth_headers("bob-token")
    )

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
    create_club(client)

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
    club = create_club(client)
    first_season = _start_season(client, member_names=["Alice"], club_id=club["id"])
    second_season = _start_season(client, member_names=["Carol"], club_id=club["id"])

    response = client.post(
        f"/seasons/{second_season['id']}/members", json={"player_name": "Alice"}
    )

    assert response.json()["id"] == first_season["member_ids"][0]


def test_add_member_creates_a_distinct_player_in_a_different_club(
    client: TestClient,
) -> None:
    first_season = _start_season(client, member_names=["Alice"])
    second_season = _start_season(client, member_names=["Carol"])

    response = client.post(
        f"/seasons/{second_season['id']}/members", json={"player_name": "Alice"}
    )

    assert response.json()["id"] != first_season["member_ids"][0]


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
    create_club(client)

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

    response = client.delete(
        f"/seasons/{season['id']}/members/{carol_id}",
        headers=auth_headers(season["organizer_token"]),
    )

    assert response.status_code == 404


def test_remove_member_for_an_unknown_season_returns_404(client: TestClient) -> None:
    create_club(client)

    response = client.delete("/seasons/999999/members/1")

    assert response.status_code == 404


# --- authorization -----------------------------------------------------
#
# Every domain test above runs as the club's organizer by default (see
# factories.create_club) because the organizer can act on anyone in
# their club, so acting as them preserves whatever behavior those tests
# actually care about. These tests are the ones that would catch it if
# that safety net had holes: a non-organizer member doing something
# only the organizer should be able to, or acting for someone who isn't
# them.


def test_missing_token_is_rejected(client: TestClient) -> None:
    response = client.post("/clubs", json={"name": "A Club"})

    assert response.status_code == 401


def test_unidentified_token_is_rejected(client: TestClient) -> None:
    """A syntactically fine bearer token that never went through
    /players/identify — there's no Player row for it to resolve to.
    """
    response = client.post(
        "/clubs",
        json={"name": "A Club"},
        headers=auth_headers("nobody-called-identify"),
    )

    assert response.status_code == 404


def test_a_member_cannot_start_a_season(client: TestClient) -> None:
    club = create_club(client)
    carol = identify(client, "Carol")
    client.post(f"/clubs/{club['id']}/join", headers=auth_headers(carol["token"]))

    response = client.post(
        f"/clubs/{club['id']}/seasons",
        json={
            "total_venue_cost": "10000",
            "game_dates": ["2026-08-18"],
            "member_names": ["Carol"],
        },
        headers=auth_headers(carol["token"]),
    )

    assert response.status_code == 403


def test_a_member_cannot_add_a_season_member(client: TestClient) -> None:
    season = _start_season(client, member_names=["Alice"])
    carol = identify(client, "Carol")
    client.post(
        f"/clubs/{season['club_id']}/join", headers=auth_headers(carol["token"])
    )

    response = client.post(
        f"/seasons/{season['id']}/members",
        json={"player_name": "Dave"},
        headers=auth_headers(carol["token"]),
    )

    assert response.status_code == 403


def test_a_member_cannot_remove_a_season_member(client: TestClient) -> None:
    season = _start_season(client, member_names=["Alice", "Bob"])
    bob_id = season["member_ids"][1]
    carol = identify(client, "Carol")
    client.post(
        f"/clubs/{season['club_id']}/join", headers=auth_headers(carol["token"])
    )

    response = client.delete(
        f"/seasons/{season['id']}/members/{bob_id}",
        headers=auth_headers(carol["token"]),
    )

    assert response.status_code == 403


def test_a_member_cannot_update_season_settings(client: TestClient) -> None:
    season = _start_season(client, member_names=["Alice"])
    carol = identify(client, "Carol")
    client.post(
        f"/clubs/{season['club_id']}/join", headers=auth_headers(carol["token"])
    )

    response = client.patch(
        f"/seasons/{season['id']}",
        json={"capacity": 20},
        headers=auth_headers(carol["token"]),
    )

    assert response.status_code == 403


def test_a_member_cannot_cancel_a_game(client: TestClient) -> None:
    season = _start_season(client, member_names=["Alice"])
    game_id = season["games"][0]["id"]
    carol = identify(client, "Carol")
    client.post(
        f"/clubs/{season['club_id']}/join", headers=auth_headers(carol["token"])
    )

    response = client.post(
        f"/games/{game_id}/cancel",
        json={"refunded": True},
        headers=auth_headers(carol["token"]),
    )

    assert response.status_code == 403


def test_a_member_cannot_view_the_join_pool(client: TestClient) -> None:
    season = _start_season(client, member_names=["Alice"])
    carol = identify(client, "Carol")
    client.post(
        f"/clubs/{season['club_id']}/join", headers=auth_headers(carol["token"])
    )

    response = client.get(
        f"/seasons/{season['id']}/join-pool", headers=auth_headers(carol["token"])
    )

    assert response.status_code == 403


def test_a_member_cannot_view_the_settlement(client: TestClient) -> None:
    season = _start_season(client, member_names=["Alice"])
    carol = identify(client, "Carol")
    client.post(
        f"/clubs/{season['club_id']}/join", headers=auth_headers(carol["token"])
    )

    response = client.get(
        f"/seasons/{season['id']}/settlement", headers=auth_headers(carol["token"])
    )

    assert response.status_code == 403


def test_a_member_cannot_settle_the_season(client: TestClient) -> None:
    season = _start_season(client, member_names=["Alice"])
    carol = identify(client, "Carol")
    client.post(
        f"/clubs/{season['club_id']}/join", headers=auth_headers(carol["token"])
    )

    response = client.post(
        f"/seasons/{season['id']}/settle", headers=auth_headers(carol["token"])
    )

    assert response.status_code == 403


def test_a_member_cannot_record_a_payment_for_someone(client: TestClient) -> None:
    season = _start_season(client, member_names=["Alice"])
    alice_id = season["member_ids"][0]
    carol = identify(client, "Carol")
    client.post(
        f"/clubs/{season['club_id']}/join", headers=auth_headers(carol["token"])
    )

    response = client.post(
        f"/clubs/{season['club_id']}/players/{alice_id}/payments",
        json={"amount": "100"},
        headers=auth_headers(carol["token"]),
    )

    assert response.status_code == 403


def test_a_member_can_record_their_own_absence(client: TestClient) -> None:
    season = _start_season(client, member_names=["Alice"])
    game_id = season["games"][0]["id"]
    carol = identify(client, "Carol")
    client.post(
        f"/clubs/{season['club_id']}/join", headers=auth_headers(carol["token"])
    )
    client.post(
        f"/seasons/{season['id']}/members",
        json={"player_name": "Carol"},
    )  # organizer promotes her (persistent header is still the organizer's)

    response = client.post(
        "/absences",
        json={"player_name": "Carol", "game_id": game_id},
        headers=auth_headers(carol["token"]),
    )

    assert response.status_code == 200


def test_a_member_cannot_record_an_absence_for_someone_else(client: TestClient) -> None:
    season = _start_season(client, member_names=["Alice", "Bob"])
    game_id = season["games"][0]["id"]
    carol = identify(client, "Carol")
    client.post(
        f"/clubs/{season['club_id']}/join", headers=auth_headers(carol["token"])
    )

    response = client.post(
        "/absences",
        json={"player_name": "Bob", "game_id": game_id},
        headers=auth_headers(carol["token"]),
    )

    assert response.status_code == 403


def test_a_member_can_sign_up_themselves_as_a_drop_in(client: TestClient) -> None:
    season = _start_season(client, member_names=["Alice"], capacity=18)
    game_id = season["games"][0]["id"]
    carol = identify(client, "Carol")
    # Joining the club first matters: _get_or_create_player resolves a
    # name to a club member, so without this "Carol" would resolve to a
    # brand new player rather than the one just identified.
    client.post(
        f"/clubs/{season['club_id']}/join", headers=auth_headers(carol["token"])
    )

    response = client.post(
        "/drop-ins",
        json={"player_name": "Carol", "game_id": game_id},
        headers=auth_headers(carol["token"]),
    )

    assert response.status_code == 200


def test_a_non_member_cannot_sign_anyone_up(client: TestClient) -> None:
    season = _start_season(client, member_names=["Alice"], capacity=18)
    game_id = season["games"][0]["id"]
    carol = identify(client, "Carol")  # never joined this club

    response = client.post(
        "/drop-ins",
        json={"player_name": "A Stranger", "game_id": game_id},
        headers=auth_headers(carol["token"]),
    )

    assert response.status_code == 403


def test_a_member_can_bring_a_guest_who_has_no_account(client: TestClient) -> None:
    # "+1, I'm bringing a friend" — the friend isn't in LINE and can't
    # tap anything, so the member bringing them signs them up.
    season = _start_season(client, member_names=["Alice"], capacity=18)
    game_id = season["games"][0]["id"]
    carol = identify(client, "Carol")
    client.post(
        f"/clubs/{season['club_id']}/join", headers=auth_headers(carol["token"])
    )

    response = client.post(
        "/drop-ins",
        json={"player_name": "Carol's Friend", "game_id": game_id},
        headers=auth_headers(carol["token"]),
    )

    assert response.status_code == 200


def test_a_member_cannot_sign_up_someone_who_has_an_account(client: TestClient) -> None:
    # A drop-in costs money. Somebody who can speak for themselves has
    # to be the one who commits to it.
    season = _start_season(client, member_names=["Alice"], capacity=18)
    game_id = season["games"][0]["id"]
    club_id = season["club_id"]
    carol = identify(client, "Carol")
    dave = identify(client, "Dave")
    for player in (carol, dave):
        client.post(f"/clubs/{club_id}/join", headers=auth_headers(player["token"]))

    response = client.post(
        "/drop-ins",
        json={"player_name": "Dave", "game_id": game_id},
        headers=auth_headers(carol["token"]),
    )

    assert response.status_code == 403


def test_the_organizer_can_still_sign_up_anyone(client: TestClient) -> None:
    season = _start_season(client, member_names=["Alice"], capacity=18)
    game_id = season["games"][0]["id"]
    dave = identify(client, "Dave")
    client.post(f"/clubs/{season['club_id']}/join", headers=auth_headers(dave["token"]))

    # client.headers still carries the organizer's token (see create_club).
    response = client.post(
        "/drop-ins", json={"player_name": "Dave", "game_id": game_id}
    )

    assert response.status_code == 200


def test_a_member_can_cancel_their_own_drop_in(client: TestClient) -> None:
    season = _start_season(client, member_names=["Alice"], capacity=18)
    game_id = season["games"][0]["id"]
    carol = identify(client, "Carol")
    client.post(
        f"/clubs/{season['club_id']}/join", headers=auth_headers(carol["token"])
    )
    signup = client.post(
        "/drop-ins",
        json={"player_name": "Carol", "game_id": game_id},
        headers=auth_headers(carol["token"]),
    ).json()

    response = client.post(
        f"/drop-ins/{signup['id']}/cancel", headers=auth_headers(carol["token"])
    )

    assert response.status_code == 200


def test_a_member_cannot_cancel_someone_elses_drop_in(client: TestClient) -> None:
    season = _start_season(client, member_names=["Alice"], capacity=18)
    game_id = season["games"][0]["id"]
    signup = client.post(
        "/drop-ins", json={"player_name": "Dave", "game_id": game_id}
    ).json()  # organizer signs Dave up
    carol = identify(client, "Carol")

    response = client.post(
        f"/drop-ins/{signup['id']}/cancel", headers=auth_headers(carol["token"])
    )

    assert response.status_code == 403


def test_a_member_can_view_their_own_ledger(client: TestClient) -> None:
    season = _start_season(client, member_names=["Alice"])
    carol = identify(client, "Carol")
    client.post(
        f"/clubs/{season['club_id']}/join", headers=auth_headers(carol["token"])
    )

    response = client.get(
        f"/clubs/{season['club_id']}/players/{carol['id']}/ledger",
        headers=auth_headers(carol["token"]),
    )

    assert response.status_code == 200


def test_a_member_cannot_view_someone_elses_ledger(client: TestClient) -> None:
    season = _start_season(client, member_names=["Alice"])
    alice_id = season["member_ids"][0]
    carol = identify(client, "Carol")
    client.post(
        f"/clubs/{season['club_id']}/join", headers=auth_headers(carol["token"])
    )

    response = client.get(
        f"/clubs/{season['club_id']}/players/{alice_id}/ledger",
        headers=auth_headers(carol["token"]),
    )

    assert response.status_code == 403


def test_the_organizer_can_view_any_members_ledger(client: TestClient) -> None:
    season = _start_season(client, member_names=["Alice"])
    alice_id = season["member_ids"][0]

    response = client.get(
        f"/clubs/{season['club_id']}/players/{alice_id}/ledger"
    )  # persistent header is still the organizer's

    assert response.status_code == 200


def test_a_stranger_cannot_join_a_club_as_someone_they_are_not(
    client: TestClient,
) -> None:
    """/clubs/{id}/join has no player_id in its body any more — the only
    identity it can act as is the verified caller's own.
    """
    club = create_club(client)
    carol = identify(client, "Carol")

    response = client.post(
        f"/clubs/{club['id']}/join",
        json={"player_id": 999999},
        headers=auth_headers(carol["token"]),
    )

    assert response.status_code == 200
    assert response.json()["name"] == "Carol"  # the body's player_id was ignored


# --- a player's own club list --------------------------------------------


def test_list_player_clubs_spans_multiple_clubs_with_roles(client: TestClient) -> None:
    club_a = create_club(client, name="Club A")
    alice = identify(client, "Alice")
    client.post(f"/clubs/{club_a['id']}/join", headers=auth_headers(alice["token"]))
    club_b = create_club(client, name="Club B")
    client.post(f"/clubs/{club_b['id']}/join", headers=auth_headers(alice["token"]))

    response = client.get(
        f"/players/{alice['id']}/clubs", headers=auth_headers(alice["token"])
    )

    assert response.status_code == 200
    body = response.json()
    assert {c["name"]: c["role"] for c in body} == {
        "Club A": "member",
        "Club B": "member",
    }


def test_list_player_clubs_includes_organizer_role(client: TestClient) -> None:
    club = create_club(client)

    response = client.get(
        f"/players/{club['organizer_id']}/clubs",
        headers=auth_headers(club["organizer_token"]),
    )

    assert response.status_code == 200
    assert response.json() == [
        {
            "id": club["id"],
            "name": club["name"],
            "role": "organizer",
            "wants_fixed_membership": None,
        }
    ]


def test_list_player_clubs_for_someone_else_returns_403(client: TestClient) -> None:
    alice = identify(client, "Alice")
    bob = identify(client, "Bob")

    response = client.get(
        f"/players/{alice['id']}/clubs", headers=auth_headers(bob["token"])
    )

    assert response.status_code == 403


# --- club-scoped reads ---------------------------------------------------
#
# These four reads were public until they weren't: between them they
# expose every member's name, gender and LINE profile picture, who took
# leave, who dropped in, and what a game costs. CLAUDE.md 2.5 says a club
# never sees another club's members, seasons or books.


def test_a_stranger_cannot_read_a_clubs_members(client: TestClient) -> None:
    season = _start_season(client, member_names=["Alice"])
    stranger = identify(client, "Stranger")

    response = client.get(
        f"/clubs/{season['club_id']}/members",
        headers=auth_headers(stranger["token"]),
    )

    assert response.status_code == 403


def test_a_stranger_cannot_list_a_clubs_seasons(client: TestClient) -> None:
    season = _start_season(client, member_names=["Alice"])
    stranger = identify(client, "Stranger")

    response = client.get(
        f"/clubs/{season['club_id']}/seasons",
        headers=auth_headers(stranger["token"]),
    )

    assert response.status_code == 403


def test_a_stranger_cannot_read_a_season(client: TestClient) -> None:
    season = _start_season(client, member_names=["Alice"])
    stranger = identify(client, "Stranger")

    response = client.get(
        f"/seasons/{season['id']}", headers=auth_headers(stranger["token"])
    )

    assert response.status_code == 403


def test_these_reads_are_rejected_without_any_identity(client: TestClient) -> None:
    season = _start_season(client, member_names=["Alice"])

    for path in (
        "/clubs",
        f"/clubs/{season['club_id']}/members",
        f"/clubs/{season['club_id']}/seasons",
        f"/seasons/{season['id']}",
    ):
        response = client.get(path, headers={"Authorization": ""})
        assert response.status_code == 401, path


def test_list_clubs_returns_only_your_own(client: TestClient) -> None:
    mine = create_club(client, name="Mine")
    my_token = mine["organizer_token"]
    create_club(client, name="Someone Else's")  # a different organizer

    body = client.get("/clubs", headers=auth_headers(my_token)).json()

    assert [c["name"] for c in body] == ["Mine"]


def test_a_club_member_can_read_the_club(client: TestClient) -> None:
    season = _start_season(client, member_names=["Alice"])
    carol = identify(client, "Carol")
    client.post(
        f"/clubs/{season['club_id']}/join", headers=auth_headers(carol["token"])
    )

    response = client.get(
        f"/seasons/{season['id']}", headers=auth_headers(carol["token"])
    )

    assert response.status_code == 200


def test_an_invite_link_can_name_a_club_you_have_not_joined(
    client: TestClient,
) -> None:
    """The whole point of GET /clubs/{id}: someone holding an invite link
    needs the club's name to decide whether to join. Nothing else about
    the club is readable until they do.
    """
    club = create_club(client, name="啪排郎")
    stranger = identify(client, "Stranger")

    response = client.get(
        f"/clubs/{club['id']}", headers=auth_headers(stranger["token"])
    )

    assert response.status_code == 200
    assert response.json() == {"id": club["id"], "name": "啪排郎"}


# --- deleting things -----------------------------------------------------
#
# Test clubs and mistaken seasons need to be removable, but settled books
# do not: CLAUDE.md 2.5 wants every data change auditable, and destroying
# finished accounts is the one change that can't be.


def test_delete_season_removes_it_and_its_games(client: TestClient) -> None:
    season = _start_season(client, member_names=["Alice"])

    response = client.delete(f"/seasons/{season['id']}")

    assert response.status_code == 204
    assert client.get(f"/seasons/{season['id']}").status_code == 404
    assert client.get(f"/clubs/{season['club_id']}/seasons").json() == []


def test_delete_season_clears_the_fees_it_charged(client: TestClient) -> None:
    season = _start_season(client, member_names=["Alice"])
    alice_id = season["member_ids"][0]
    before = client.get(f"/clubs/{season['club_id']}/players/{alice_id}/ledger").json()
    assert before["entries"], "the season fee should have been charged up front"

    client.delete(f"/seasons/{season['id']}")

    after = client.get(f"/clubs/{season['club_id']}/players/{alice_id}/ledger").json()
    assert after["entries"] == []
    assert after["balance"] == "0"


def test_delete_season_rejects_a_settled_one(client: TestClient) -> None:
    season = _start_season(client, member_names=["Alice"])
    client.post(f"/seasons/{season['id']}/settle")

    response = client.delete(f"/seasons/{season['id']}")

    assert response.status_code == 400


def test_a_member_cannot_delete_a_season(client: TestClient) -> None:
    season = _start_season(client, member_names=["Alice"])
    carol = identify(client, "Carol")
    client.post(
        f"/clubs/{season['club_id']}/join", headers=auth_headers(carol["token"])
    )

    response = client.delete(
        f"/seasons/{season['id']}", headers=auth_headers(carol["token"])
    )

    assert response.status_code == 403


def test_delete_club_removes_its_seasons_too(client: TestClient) -> None:
    season = _start_season(client, member_names=["Alice"])

    response = client.delete(f"/clubs/{season['club_id']}")

    assert response.status_code == 204
    assert client.get("/clubs").json() == []
    assert client.get(f"/seasons/{season['id']}").status_code == 404


def test_delete_club_rejects_when_a_season_is_settled(client: TestClient) -> None:
    season = _start_season(client, member_names=["Alice"])
    client.post(f"/seasons/{season['id']}/settle")

    response = client.delete(f"/clubs/{season['club_id']}")

    assert response.status_code == 400


def test_delete_club_keeps_the_players_themselves(client: TestClient) -> None:
    """A Player is global and outlives any one club — CLAUDE.md 2.1."""
    club = create_club(client)
    alice = identify(client, "Alice")
    client.post(f"/clubs/{club['id']}/join", headers=auth_headers(alice["token"]))

    client.delete(f"/clubs/{club['id']}")

    still_there = client.put(
        f"/players/{alice['id']}/gender",
        json={"gender": "female"},
        headers=auth_headers(alice["token"]),
    )
    assert still_there.status_code == 200


def test_remove_club_member_takes_them_out_of_the_club(client: TestClient) -> None:
    club = create_club(client)
    carol = identify(client, "Carol")
    client.post(f"/clubs/{club['id']}/join", headers=auth_headers(carol["token"]))

    response = client.delete(f"/clubs/{club['id']}/members/{carol['id']}")

    assert response.status_code == 204
    assert [m["name"] for m in client.get(f"/clubs/{club['id']}/members").json()] == [
        "Test Organizer"
    ]


def test_remove_club_member_refuses_while_they_are_on_a_roster(
    client: TestClient,
) -> None:
    """Their season membership drives everyone's share; that removal has
    to go through the season endpoint, which corrects the charges."""
    season = _start_season(client, member_names=["Alice", "Bob"])
    bob_id = season["member_ids"][1]

    response = client.delete(f"/clubs/{season['club_id']}/members/{bob_id}")

    assert response.status_code == 400


def test_remove_club_member_refuses_the_last_organizer(client: TestClient) -> None:
    club = create_club(client)

    response = client.delete(f"/clubs/{club['id']}/members/{club['organizer_id']}")

    assert response.status_code == 400


def test_an_organizer_can_set_gender_for_someone_with_no_line_account(
    client: TestClient,
) -> None:
    """Alice was typed in by hand, so she has no way to open the app and
    set this herself, and the roster's male/female count would never be
    right without it."""
    season = _start_season(client, member_names=["Alice"])
    alice_id = season["member_ids"][0]

    response = client.put(f"/players/{alice_id}/gender", json={"gender": "female"})

    assert response.status_code == 200
    assert response.json()["gender"] == "female"


def test_an_organizer_cannot_set_gender_for_someone_with_a_line_account(
    client: TestClient,
) -> None:
    club = create_club(client)
    carol = identify(client, "Carol")
    client.post(f"/clubs/{club['id']}/join", headers=auth_headers(carol["token"]))

    response = client.put(f"/players/{carol['id']}/gender", json={"gender": "female"})

    assert response.status_code == 403


def test_a_stranger_cannot_set_gender_for_an_accountless_player(
    client: TestClient,
) -> None:
    season = _start_season(client, member_names=["Alice"])
    alice_id = season["member_ids"][0]
    stranger = identify(client, "Stranger")

    response = client.put(
        f"/players/{alice_id}/gender",
        json={"gender": "female"},
        headers=auth_headers(stranger["token"]),
    )

    assert response.status_code == 403


# --- linking a LINE account to a roster entry ----------------------------
#
# The organizer types someone in before they've ever opened the app, then
# that person logs in and identify_player — which never guesses identity
# from a name — creates a second row for them. This is the manual
# reconciliation the docstring there always promised.


def test_link_moves_the_line_identity_onto_the_roster_entry(
    client: TestClient,
) -> None:
    season = _start_season(client, member_names=["吳亞彤"])
    typed_in_id = season["member_ids"][0]
    with_line = identify(client, "吳亞彤")
    client.post(
        f"/clubs/{season['club_id']}/join", headers=auth_headers(with_line["token"])
    )

    response = client.post(
        f"/clubs/{season['club_id']}/players/{typed_in_id}/link",
        json={"line_player_id": with_line["id"]},
    )

    assert response.status_code == 200
    assert response.json()["linked"] is True
    members = client.get(f"/clubs/{season['club_id']}/members").json()
    assert [m["id"] for m in members if m["name"].startswith("吳亞彤")] == [typed_in_id]


def test_link_keeps_the_roster_entrys_ledger(client: TestClient) -> None:
    """The whole point of merging onto the typed-in row: it already owns
    the season fee, and that history has to survive."""
    season = _start_season(client, member_names=["吳亞彤"])
    typed_in_id = season["member_ids"][0]
    before = client.get(
        f"/clubs/{season['club_id']}/players/{typed_in_id}/ledger"
    ).json()["balance"]
    with_line = identify(client, "吳亞彤")
    client.post(
        f"/clubs/{season['club_id']}/join", headers=auth_headers(with_line["token"])
    )

    client.post(
        f"/clubs/{season['club_id']}/players/{typed_in_id}/link",
        json={"line_player_id": with_line["id"]},
    )

    after = client.get(
        f"/clubs/{season['club_id']}/players/{typed_in_id}/ledger"
    ).json()
    assert after["balance"] == before


def test_after_linking_that_person_can_act_as_themselves(client: TestClient) -> None:
    season = _start_season(client, member_names=["吳亞彤"])
    typed_in_id = season["member_ids"][0]
    with_line = identify(client, "吳亞彤")
    client.post(
        f"/clubs/{season['club_id']}/join", headers=auth_headers(with_line["token"])
    )
    client.post(
        f"/clubs/{season['club_id']}/players/{typed_in_id}/link",
        json={"line_player_id": with_line["id"]},
    )

    # Their token now resolves to the roster entry, so recording their own
    # absence is a self-action rather than something only the organizer can do.
    response = client.post(
        "/absences",
        json={"player_name": "吳亞彤", "game_id": season["games"][0]["id"]},
        headers=auth_headers(with_line["token"]),
    )

    assert response.status_code == 200


def test_link_refuses_when_the_line_account_has_its_own_history(
    client: TestClient,
) -> None:
    """Deleting it would destroy real records — the duplicate to remove
    is the roster entry, not this one."""
    season = _start_season(client, member_names=["吳亞彤"])
    typed_in_id = season["member_ids"][0]
    with_line = identify(client, "吳亞彤")
    client.post(
        f"/clubs/{season['club_id']}/join", headers=auth_headers(with_line["token"])
    )
    client.post(
        "/drop-ins",
        json={"player_name": with_line["name"], "game_id": season["games"][0]["id"]},
    )

    response = client.post(
        f"/clubs/{season['club_id']}/players/{typed_in_id}/link",
        json={"line_player_id": with_line["id"]},
    )

    assert response.status_code == 400


def test_link_refuses_an_already_linked_roster_entry(client: TestClient) -> None:
    club = create_club(client)
    alice = identify(client, "Alice")
    client.post(f"/clubs/{club['id']}/join", headers=auth_headers(alice["token"]))
    bob = identify(client, "Bob")
    client.post(f"/clubs/{club['id']}/join", headers=auth_headers(bob["token"]))

    response = client.post(
        f"/clubs/{club['id']}/players/{alice['id']}/link",
        json={"line_player_id": bob["id"]},
    )

    assert response.status_code == 400


def test_a_member_cannot_link_players(client: TestClient) -> None:
    season = _start_season(client, member_names=["吳亞彤"])
    typed_in_id = season["member_ids"][0]
    carol = identify(client, "Carol")
    client.post(
        f"/clubs/{season['club_id']}/join", headers=auth_headers(carol["token"])
    )

    response = client.post(
        f"/clubs/{season['club_id']}/players/{typed_in_id}/link",
        json={"line_player_id": carol["id"]},
        headers=auth_headers(carol["token"]),
    )

    assert response.status_code == 403


# --- what a newcomer says they are ---------------------------------------
#
# Claiming to be a fixed member queues you for the organizer rather than
# putting you on a roster: a season fee is a real obligation, and who
# owes what is the organizer's call. Saying you're not opens drop-in
# signups straight away, which commit you one game at a time.


def test_a_newcomer_starts_with_no_stated_intent(client: TestClient) -> None:
    club = create_club(client)
    carol = identify(client, "Carol")
    client.post(f"/clubs/{club['id']}/join", headers=auth_headers(carol["token"]))

    body = client.get(
        f"/players/{carol['id']}/clubs", headers=auth_headers(carol["token"])
    ).json()

    assert body[0]["wants_fixed_membership"] is None


def test_saying_you_are_a_fixed_member_does_not_put_you_on_a_roster(
    client: TestClient,
) -> None:
    """It only queues them — being on the roster is what a season fee is
    charged against, and that stays the organizer's decision."""
    season = _start_season(client, member_names=["Alice"])
    carol = identify(client, "Carol")
    client.post(
        f"/clubs/{season['club_id']}/join", headers=auth_headers(carol["token"])
    )

    response = client.put(
        f"/clubs/{season['club_id']}/members/me/intent",
        json={"wants_fixed_membership": True},
        headers=auth_headers(carol["token"]),
    )

    assert response.status_code == 200
    assert response.json()["wants_fixed_membership"] is True
    members = client.get(f"/seasons/{season['id']}").json()["members"]
    assert "Carol" not in [m["name"] for m in members]
    ledger = client.get(
        f"/clubs/{season['club_id']}/players/{carol['id']}/ledger",
        headers=auth_headers(carol["token"]),
    ).json()
    assert ledger["balance"] == "0", "stating an intention must never charge anyone"


def test_the_organizer_sees_who_is_waiting(client: TestClient) -> None:
    season = _start_season(client, member_names=["Alice"])
    carol = identify(client, "Carol")
    client.post(
        f"/clubs/{season['club_id']}/join", headers=auth_headers(carol["token"])
    )
    client.put(
        f"/clubs/{season['club_id']}/members/me/intent",
        json={"wants_fixed_membership": True},
        headers=auth_headers(carol["token"]),
    )

    members = client.get(f"/clubs/{season['club_id']}/members").json()

    carol_row = next(m for m in members if m["name"] == "Carol")
    assert carol_row["wants_fixed_membership"] is True


def test_intent_can_be_changed_later(client: TestClient) -> None:
    club = create_club(client)
    carol = identify(client, "Carol")
    client.post(f"/clubs/{club['id']}/join", headers=auth_headers(carol["token"]))

    for value in (False, True, False):
        response = client.put(
            f"/clubs/{club['id']}/members/me/intent",
            json={"wants_fixed_membership": value},
            headers=auth_headers(carol["token"]),
        )
        assert response.json()["wants_fixed_membership"] is value


def test_intent_needs_you_to_be_in_the_club(client: TestClient) -> None:
    club = create_club(client)
    stranger = identify(client, "Stranger")

    response = client.put(
        f"/clubs/{club['id']}/members/me/intent",
        json={"wants_fixed_membership": True},
        headers=auth_headers(stranger["token"]),
    )

    assert response.status_code == 403


def test_the_join_pool_shows_who_asked_to_be_a_fixed_member(
    client: TestClient,
) -> None:
    """The pool is the organizer's queue; a request they can't see is a
    person left waiting."""
    season = _start_season(client, member_names=["Alice"])
    carol = identify(client, "Carol")
    client.post(
        f"/clubs/{season['club_id']}/join", headers=auth_headers(carol["token"])
    )
    client.put(
        f"/clubs/{season['club_id']}/members/me/intent",
        json={"wants_fixed_membership": True},
        headers=auth_headers(carol["token"]),
    )
    dave = identify(client, "Dave")
    client.post(f"/clubs/{season['club_id']}/join", headers=auth_headers(dave["token"]))
    client.put(
        f"/clubs/{season['club_id']}/members/me/intent",
        json={"wants_fixed_membership": False},
        headers=auth_headers(dave["token"]),
    )

    pool = client.get(f"/seasons/{season['id']}/join-pool").json()

    by_name = {p["name"]: p for p in pool}
    assert by_name["Carol"]["wants_fixed_membership"] is True
    assert by_name["Dave"]["wants_fixed_membership"] is False


# --- reporting a problem ---------------------------------------------------


def test_a_report_reaches_the_developer_with_its_context(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """What makes a report actionable is who and where, which the page
    attaches rather than asking someone to type."""
    sent: list[tuple[str, str]] = []
    monkeypatch.setattr(
        routes, "push_to_user", lambda uid, text: sent.append((uid, text))
    )
    monkeypatch.setenv("LINE_ORGANIZER_USER_ID", "U-dev")
    club = create_club(client, name="啪排郎")
    alice = identify(client, "Alice")

    response = client.post(
        "/reports",
        json={
            "message": "帳務頁的金額不對",
            "page": "organizer-ledger.html",
            "user_agent": "iPhone LINE",
            "club_id": club["id"],
        },
        headers=auth_headers(alice["token"]),
    )

    assert response.status_code == 204
    assert len(sent) == 1
    user_id, text = sent[0]
    assert user_id == "U-dev"
    assert "帳務頁的金額不對" in text
    assert "Alice" in text
    assert "啪排郎" in text
    assert "organizer-ledger.html" in text


def test_an_empty_report_is_refused(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LINE_ORGANIZER_USER_ID", "U-dev")
    alice = identify(client, "Alice")

    response = client.post(
        "/reports", json={"message": "   "}, headers=auth_headers(alice["token"])
    )

    assert response.status_code == 400


def test_a_report_that_cannot_be_delivered_says_so(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nothing stores these, so a silent failure would lose the report
    outright — the reporter has to find out here."""

    def explode(user_id: str, text: str) -> None:
        raise RuntimeError("LINE quota exhausted")

    monkeypatch.setattr(routes, "push_to_user", explode)
    monkeypatch.setenv("LINE_ORGANIZER_USER_ID", "U-dev")
    alice = identify(client, "Alice")

    response = client.post(
        "/reports", json={"message": "壞掉了"}, headers=auth_headers(alice["token"])
    )

    assert response.status_code == 502


def test_reporting_requires_an_identity(client: TestClient) -> None:
    response = client.post(
        "/reports", json={"message": "壞掉了"}, headers={"Authorization": ""}
    )

    assert response.status_code == 401


def test_a_report_with_a_screenshot_sends_the_picture_too(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """LINE renders an image by fetching a URL from its own servers, so
    the picture has to be reachable without any of our credentials."""
    sent_text: list[str] = []
    sent_images: list[str] = []
    monkeypatch.setattr(routes, "push_to_user", lambda uid, t: sent_text.append(t))
    monkeypatch.setattr(
        routes, "push_image_to_user", lambda uid, url: sent_images.append(url)
    )
    monkeypatch.setenv("LINE_ORGANIZER_USER_ID", "U-dev")
    alice = identify(client, "Alice")
    # a 1x1 PNG
    png = (
        "data:image/png;base64,"
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
    )

    response = client.post(
        "/reports",
        json={"message": "畫面壞了", "screenshot": png},
        headers=auth_headers(alice["token"]),
    )

    assert response.status_code == 204
    assert len(sent_images) == 1
    # The image must be readable with no auth at all, the way LINE fetches it.
    fetched = client.get(sent_images[0].replace("http://testserver", ""))
    assert fetched.status_code == 200
    assert fetched.headers["content-type"] == "image/png"


def test_a_screenshot_that_is_not_an_image_is_refused(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(routes, "push_to_user", lambda uid, t: None)
    monkeypatch.setenv("LINE_ORGANIZER_USER_ID", "U-dev")
    alice = identify(client, "Alice")

    response = client.post(
        "/reports",
        json={"message": "x", "screenshot": "data:text/html;base64,PHNjcmlwdD4="},
        headers=auth_headers(alice["token"]),
    )

    assert response.status_code == 400


def test_an_unknown_screenshot_token_is_a_404(client: TestClient) -> None:
    response = client.get("/reports/not-a-real-token/image")

    assert response.status_code == 404


def test_signing_up_a_group_charges_each_of_them(client: TestClient) -> None:
    season = _start_season(client, member_names=["Alice"], capacity=18)
    game_id = season["games"][0]["id"]
    carol = identify(client, "Carol")
    client.post(
        f"/clubs/{season['club_id']}/join", headers=auth_headers(carol["token"])
    )

    response = client.post(
        f"/games/{game_id}/drop-ins",
        json={
            "people": [
                {"player_name": "Carol", "player_id": carol["id"]},
                {"player_name": "小明", "gender": "male"},
                {"player_name": "小華", "gender": "female"},
            ]
        },
        headers=auth_headers(carol["token"]),
    )

    assert response.status_code == 200
    results = response.json()["results"]
    assert [r["status"] for r in results] == ["confirmed"] * 3


def test_two_guests_with_the_same_name_are_two_different_people(
    client: TestClient,
) -> None:
    # Two real people are both called 小明. Matching a typed name to an
    # existing player would let only the first of them play.
    season = _start_season(client, member_names=["Alice"], capacity=18)
    game_id = season["games"][0]["id"]

    response = client.post(
        f"/games/{game_id}/drop-ins",
        json={
            "people": [
                {"player_name": "小明", "gender": "male"},
                {"player_name": "小明", "gender": "female"},
            ]
        },
    )

    assert response.status_code == 200
    results = response.json()["results"]
    assert results[0]["player_id"] != results[1]["player_id"]
    assert [r["status"] for r in results] == ["confirmed", "confirmed"]


def test_a_group_overflowing_capacity_waitlists_the_last_ones(
    client: TestClient,
) -> None:
    # Capacity is spent in list order, so the caller knows in advance
    # which of their friends misses out.
    season = _start_season(client, member_names=["Alice", "Bob"], capacity=3)
    game_id = season["games"][0]["id"]

    response = client.post(
        f"/games/{game_id}/drop-ins",
        json={
            "people": [
                {"player_name": "第一個", "gender": "male"},
                {"player_name": "第二個", "gender": "male"},
                {"player_name": "第三個", "gender": "female"},
            ]
        },
    )

    assert response.status_code == 200
    assert [r["status"] for r in response.json()["results"]] == [
        "confirmed",
        "waitlisted",
        "waitlisted",
    ]


def test_a_group_naming_a_fixed_member_is_rejected_whole(client: TestClient) -> None:
    # Half-succeeding is the worst state to leave money in, so one bad
    # entry rolls the whole group back.
    season = _start_season(client, member_names=["Alice"], capacity=18)
    game_id = season["games"][0]["id"]
    alice_id = client.get(f"/seasons/{season['id']}").json()["members"][0]["id"]

    response = client.post(
        f"/games/{game_id}/drop-ins",
        json={
            "people": [
                {"player_name": "小明", "gender": "male"},
                {"player_name": "Alice", "player_id": alice_id},
            ]
        },
    )

    assert response.status_code == 400
    assert "fixed member" in response.json()["detail"]

    detail = client.get(f"/seasons/{season['id']}").json()
    game = next(g for g in detail["games"] if g["id"] == game_id)
    assert game["confirmed_drop_ins"] == [], "小明 must not have been left signed up"


def test_signing_up_a_new_person_without_a_gender_is_rejected(
    client: TestClient,
) -> None:
    # The roster's 男/女 tags are what the team is picked from.
    season = _start_season(client, member_names=["Alice"], capacity=18)
    game_id = season["games"][0]["id"]

    response = client.post(
        f"/games/{game_id}/drop-ins",
        json={"people": [{"player_name": "小明"}]},
    )

    assert response.status_code == 422


def test_a_guest_records_who_brought_them_but_you_dont(
    client: TestClient, db_session: Session
) -> None:
    # The fee lands on the guest's own ledger, but the guest has no
    # account and pays nothing — the member who brought them hands over
    # the cash, so the organizer needs to know who that was.
    season = _start_season(client, member_names=["Alice"], capacity=18)
    game_id = season["games"][0]["id"]
    carol = identify(client, "Carol")
    client.post(
        f"/clubs/{season['club_id']}/join", headers=auth_headers(carol["token"])
    )

    results = client.post(
        f"/games/{game_id}/drop-ins",
        json={
            "people": [
                {"player_name": "Carol", "player_id": carol["id"]},
                {"player_name": "小明", "gender": "male"},
            ]
        },
        headers=auth_headers(carol["token"]),
    ).json()["results"]

    brought_by = {
        r["player_id"]: db_session.get(DropInRow, r["id"]).brought_by_player_id
        for r in results
    }

    assert brought_by[carol["id"]] is None, "signing yourself up names nobody"
    guest_id = next(pid for pid in brought_by if pid != carol["id"])
    assert brought_by[guest_id] == carol["id"]


def test_a_cancelled_absence_stops_freeing_up_a_slot(client: TestClient) -> None:
    # expected = members - absences + drop-ins. Counting an absence that
    # was cancelled made the game look emptier than it is, and admitted
    # one drop-in too many for every absence that had ever been undone.
    season = _start_season(client, member_names=["Alice", "Bob"], capacity=2)
    game_id = season["games"][0]["id"]

    absence = client.post(
        "/absences", json={"player_name": "Alice", "game_id": game_id}
    ).json()
    client.post(f"/absences/{absence['id']}/cancel")

    response = client.post(
        f"/games/{game_id}/drop-ins",
        json={"people": [{"player_name": "小明", "gender": "male"}]},
    )

    assert response.status_code == 200
    assert response.json()["results"][0]["status"] == "waitlisted"


def test_the_money_screen_names_who_brought_each_guest(client: TestClient) -> None:
    season = _start_season(client, member_names=["Alice"], capacity=18)
    club_id = season["club_id"]
    game_id = season["games"][0]["id"]
    carol = identify(client, "Carol")
    client.post(f"/clubs/{club_id}/join", headers=auth_headers(carol["token"]))

    results = client.post(
        f"/games/{game_id}/drop-ins",
        json={
            "people": [
                {"player_name": "Carol", "player_id": carol["id"]},
                {"player_name": "小明", "gender": "male"},
            ]
        },
        headers=auth_headers(carol["token"]),
    ).json()["results"]
    guest_id = next(r["player_id"] for r in results if r["player_id"] != carol["id"])

    balances = client.get(f"/clubs/{club_id}/balances").json()
    by_player = {b["player_id"]: b for b in balances}

    assert by_player[guest_id]["brought_by"] == "Carol"
    assert by_player[carol["id"]]["brought_by"] is None


def test_the_organizer_can_fix_a_typo_in_a_guests_name(client: TestClient) -> None:
    # The guest has no account to correct it from themselves, and the
    # gender on the very same row was always the organizer's to edit.
    season = _start_season(client, member_names=["Alice"], capacity=18)
    game_id = season["games"][0]["id"]
    guest_id = client.post(
        f"/games/{game_id}/drop-ins",
        json={"people": [{"player_name": "小名", "gender": "male"}]},
    ).json()["results"][0]["player_id"]

    response = client.put(f"/players/{guest_id}/name", json={"name": "小明"})

    assert response.status_code == 200
    assert response.json()["name"] == "小明"


def test_an_organizer_still_cannot_rename_someone_with_an_account(
    client: TestClient,
) -> None:
    season = _start_season(client, member_names=["Alice"], capacity=18)
    carol = identify(client, "Carol")
    client.post(
        f"/clubs/{season['club_id']}/join", headers=auth_headers(carol["token"])
    )

    # client.headers still carries the organizer's token.
    response = client.put(f"/players/{carol['id']}/name", json={"name": "Renamed"})

    assert response.status_code == 403
