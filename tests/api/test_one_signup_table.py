"""Playing and waiting are one table (2026-10-07).

A move between court and queue changes a signup's `status` and nothing
else. Before, it deleted a row in one table and copied fields into a new
row in the other, along at least five paths, and two real bugs were a
field one of them forgot. These pin the new shape: the same row, every
field intact, and the two lists never mistaken for each other.
"""

from datetime import datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.api.factories import auth_headers, identify, join_club, start_season
from volleyflow.api.conversion import drop_in_from_row
from volleyflow.db.models import QUEUED, DropInRow


def _full_game_with_a_queued_guest(
    client: TestClient,
) -> tuple[dict[str, Any], int, dict[str, Any]]:
    season = start_season(
        client, member_names=["固定甲", "固定乙"], capacity=2, game_dates=["2031-01-07"]
    )
    game_id = season["games"][0]["id"]
    host = identify(client, "會員丙")
    join_club(client, season["club_id"], auth_headers(host["token"]))
    queued = client.post(
        f"/games/{game_id}/drop-ins",
        json={"people": [{"player_name": "朋友丁", "gender": "male"}]},
        headers=auth_headers(host["token"]),
    ).json()["results"][0]
    assert queued["status"] == "waitlisted"
    return season, game_id, queued


def test_a_promotion_moves_the_same_row_and_keeps_every_field(
    client: TestClient, db_session: Session
) -> None:
    _season, game_id, queued = _full_game_with_a_queued_guest(client)
    before = db_session.get(DropInRow, queued["id"])
    assert before is not None
    bringer, queued_at = before.brought_by_player_id, before.queued_at

    client.post("/absences", json={"player_name": "固定甲", "game_id": game_id})

    db_session.expire_all()
    after = db_session.get(DropInRow, queued["id"])
    assert after is not None
    assert after.status == "playing", "the row itself is now on court"
    assert after.brought_by_player_id == bringer
    assert after.queued_at == queued_at
    assert after.signed_up_at == queued_at, "they keep the precedence they waited for"


def test_back_to_the_queue_is_the_same_row_too(
    client: TestClient, db_session: Session
) -> None:
    _season, game_id, queued = _full_game_with_a_queued_guest(client)
    absence = client.post(
        "/absences", json={"player_name": "固定甲", "game_id": game_id}
    ).json()

    client.post(f"/absences/{absence['id']}/cancel")

    db_session.expire_all()
    row = db_session.get(DropInRow, queued["id"])
    assert row is not None
    assert (row.status, row.cancelled_at) == (QUEUED, None)
    assert db_session.query(DropInRow).filter(DropInRow.game_id == game_id).count() == 1


def test_a_queued_id_is_not_a_place_on_court_and_the_other_way_round(
    client: TestClient,
) -> None:
    # They share a table and a sequence now; each route only ever acts on
    # its own list, or one could cancel somebody out of the wrong one.
    season, game_id, queued = _full_game_with_a_queued_guest(client)
    on_court = client.post(
        "/drop-ins", json={"player_name": "另一位", "game_id": game_id}
    ).json()
    assert on_court["status"] == "waitlisted"  # still full: queued as well
    absence = client.post(
        "/absences", json={"player_name": "固定甲", "game_id": game_id}
    ).json()
    assert absence["id"]
    playing_id = queued["id"]  # promoted by the absence
    waiting_id = on_court["id"]

    as_drop_in = client.post(f"/drop-ins/{waiting_id}/cancel")
    as_queue = client.post(f"/waitlist/{playing_id}/cancel")

    assert as_drop_in.status_code == 404
    assert as_queue.status_code == 404
    assert season["id"]


def test_the_billing_engine_refuses_a_queued_signup() -> None:
    # Somebody waiting covers no absence and owes no share. A query that
    # forgot to leave the queue out would otherwise refund an absence
    # nobody filled, silently.
    row = DropInRow(
        id=1,
        player_id=1,
        game_id=1,
        signed_up_at=datetime(2031, 1, 1),
        queued_at=datetime(2031, 1, 1),
        status=QUEUED,
    )

    with pytest.raises(ValueError, match="queued"):
        drop_in_from_row(row, {}, {}, {})


def test_the_roster_carries_each_line_users_picture(client: TestClient) -> None:
    # Shown in place of their initial (2026-10-07); nobody without LINE
    # has one.
    season = start_season(
        client, member_names=["固定甲"], capacity=2, game_dates=["2031-01-07"]
    )
    game_id = season["games"][0]["id"]
    pic = client.post(
        "/players/identify",
        json={
            "id_token": "token-pic",
            "display_name": "有頭貼",
            "picture_url": "https://profile.line-scdn.net/pic",
        },
    ).json()
    join_club(client, season["club_id"], auth_headers("token-pic"))
    client.post(
        "/drop-ins",
        json={"player_name": "有頭貼", "game_id": game_id},
        headers=auth_headers("token-pic"),
    )
    client.post("/drop-ins", json={"player_name": "沒帳號", "game_id": game_id})

    game = client.get(f"/seasons/{season['id']}").json()["games"][0]

    assert pic["avatar_url"] == "https://profile.line-scdn.net/pic"
    on_court = {d["player_name"]: d["avatar_url"] for d in game["confirmed_drop_ins"]}
    queued = {w["player_name"]: w["avatar_url"] for w in game["waitlist_entries"]}
    assert on_court == {"有頭貼": "https://profile.line-scdn.net/pic"}
    assert queued == {"沒帳號": None}
