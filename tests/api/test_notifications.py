"""The routes that send a LINE message actually send it.

tests/notify/test_reminders.py proves the notifier builds the right
message for the right person. It cannot prove that any *route* calls it,
and that distinction was not academic: when these pushes were first
wired up on 2026-09-25 the whole suite went green without a single one
of them running. Every API test reached the real line_client, raised
KeyError on the missing LINE_CHANNEL_ACCESS_TOKEN *inside the notifier's
own `except Exception`*, and passed — green because nothing happened,
not because anything was right. These walk the real endpoints and assert
on what came out.

Recipients here are identified players, and that is the whole setup.
Somebody the organizer typed in by hand (`member_names=["Alice"]`) has
no LINE account, so the notifier correctly skips them and sent_messages
comes back empty whether the route called it or not — a test built the
usual way would prove nothing at all. `identify` gives a player a LINE
identity; the fake verify_id_token makes their token *be* their
line_user_id, which is why the assertions below compare against tokens.
"""

from datetime import date, datetime, timedelta
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.api.factories import auth_headers, identify, join_club, start_season
from volleyflow.db.models import GameRow
from volleyflow.notify import reminders

SentMessages = list[tuple[str, str]]


def _in_days(days: int) -> str:
    """A date that many days from now, as the API writes one.

    Local parts, never a UTC moment: Taiwan is UTC+8, so going through
    UTC can move the day. Used only where a test needs a game that hasn't
    been played yet — see the roster-removal test for why a written-down
    date won't do there.
    """
    return (date.today() + timedelta(days=days)).isoformat()


def _month_day(iso: str) -> str:
    """How the messages write a date: 10/7, not 2026-10-07."""
    _, month, day = iso.split("-")
    return f"{int(month)}/{int(day)}"


def _queued_behind_a_full_game(
    client: TestClient, name: str = "Carol"
) -> tuple[dict[str, str | int], int, dict[str, str]]:
    """A game filled by its two members, with one identified player
    waiting behind them."""
    season = start_season(
        client, member_names=["Alice", "Bob"], capacity=2, game_dates=["2031-08-19"]
    )
    game_id = season["games"][0]["id"]
    person = identify(client, name)
    join_club(client, season["club_id"], auth_headers(person["token"]))

    queued = client.post(
        "/drop-ins",
        json={"player_name": name, "game_id": game_id},
        headers=auth_headers(person["token"]),
    ).json()
    assert queued["status"] == "waitlisted", "the setup must really produce a queue"

    return season, game_id, person


def _past_the_deadline(db_session: Session, game_id: int) -> None:
    """As if the deadline job had already run for this game."""
    game = db_session.get(GameRow, game_id)
    assert game is not None
    game.roster_notice_sent_at = datetime.now()
    db_session.commit()


def test_a_promotion_before_the_deadline_is_held_until_it(
    client: TestClient, sent_messages: SentMessages, db_session: Session
) -> None:
    """Somebody takes leave, the queue fills the slot — and nobody is
    told yet, because a 代打 named an hour later could undo it
    (2026-10-05). The deadline job tells whoever is promoted by then."""
    _season, game_id, carol = _queued_behind_a_full_game(client)

    client.post("/absences", json={"player_name": "Alice", "game_id": game_id})
    assert sent_messages == [], "held until the deadline"

    # 2031-08-19 has no start time, and the test factory's deadline is
    # 0 hours, so it passes at 00:00 on the 19th.
    reminders.send_deadline_notices(db_session, datetime(2031, 8, 19, 0, 5))

    promoted = [text for user_id, text in sent_messages if user_id == carol["token"]]
    assert len(promoted) == 1
    assert "遞補上場：Carol" in promoted[0]
    assert "場次：8/19（二）" in promoted[0]


def test_after_the_deadline_a_promotion_is_told_at_once(
    client: TestClient, sent_messages: SentMessages, db_session: Session
) -> None:
    # The batch has gone and only the organizer can still change the
    # game, so whoever comes off the queue now hears straight away.
    _season, game_id, carol = _queued_behind_a_full_game(client)
    _past_the_deadline(db_session, game_id)

    client.post("/absences", json={"player_name": "Alice", "game_id": game_id})

    assert [user_id for user_id, _ in sent_messages] == [carol["token"]]


def test_a_promoted_guest_is_announced_to_whoever_signed_them_up(
    client: TestClient, sent_messages: SentMessages, db_session: Session
) -> None:
    season = start_season(
        client, member_names=["Alice"], capacity=1, game_dates=["2031-08-19"]
    )
    game_id = season["games"][0]["id"]
    host = identify(client, "Host")
    join_club(client, season["club_id"], auth_headers(host["token"]))
    client.post(
        f"/games/{game_id}/drop-ins",
        json={"people": [{"player_name": "Host的朋友", "gender": "male"}]},
        headers=auth_headers(host["token"]),
    )
    _past_the_deadline(db_session, game_id)

    client.post("/absences", json={"player_name": "Alice", "game_id": game_id})

    [(user_id, text)] = sent_messages
    assert user_id == host["token"], "the guest has no LINE; the host answers for them"
    assert "遞補上場：Host的朋友" in text
    assert "報名人：Host" in text


def test_taking_somebody_off_the_roster_promotes_into_each_night_separately(
    client: TestClient, sent_messages: SentMessages, db_session: Session
) -> None:
    """One action can promote people into several nights, so the
    notifier takes (game_id, player_id) pairs."""
    # Future dates, computed: the queue skips games already played.
    first, second = _in_days(7), _in_days(14)
    season = start_season(
        client, member_names=["Alice", "Bob"], capacity=2, game_dates=[first, second]
    )
    carol = identify(client, "Carol")
    join_club(client, season["club_id"], auth_headers(carol["token"]))
    for game in season["games"]:
        queued = client.post(
            "/drop-ins",
            json={"player_name": "Carol", "game_id": game["id"]},
            headers=auth_headers(carol["token"]),
        ).json()
        assert queued["status"] == "waitlisted"
        _past_the_deadline(db_session, game["id"])
    members = client.get(f"/seasons/{season['id']}").json()["members"]
    alice = next(m for m in members if m["name"] == "Alice")

    removed = client.delete(f"/seasons/{season['id']}/members/{alice['id']}")

    assert removed.status_code == 204, removed.text
    dates = sorted(text for _user_id, text in sent_messages)
    assert len(dates) == 2, "Alice's place opened at both games"
    assert _month_day(first) in dates[0]
    assert _month_day(second) in dates[1]


def test_calling_a_game_off_tells_the_people_expected_at_it(
    client: TestClient, sent_messages: SentMessages
) -> None:
    season = start_season(
        client, member_names=["Test Organizer", "Bob"], game_dates=[_in_days(7)]
    )

    response = client.post(
        f"/games/{season['games'][0]['id']}/cancel", json={"refunded": True}
    )

    assert response.status_code == 200, response.text
    assert [user_id for user_id, _ in sent_messages] == [season["organizer_token"]]
    assert "場次取消" in sent_messages[0][1]


def _settled_season(client: TestClient) -> dict[str, Any]:
    # The organizer is a season member here on purpose: create_club gave
    # them a LINE identity, and "Bob" — typed in by hand — has none, so
    # the organizer is the only member a push can reach.
    season = start_season(
        client,
        member_names=["Test Organizer", "Bob"],
        capacity=2,
        game_dates=["2026-08-18"],
    )
    assert client.post(f"/seasons/{season['id']}/settle", json={}).status_code == 200
    return season


def test_settling_a_season_says_nothing_by_itself(
    client: TestClient, sent_messages: SentMessages
) -> None:
    """Settling used to push to every member, and later offered a
    settlement notice of its own. Both are gone: whatever a member keeps
    for next season is told once, in next season's 繳費通知."""
    _settled_season(client)

    assert sent_messages == []


def _next_season(client: TestClient, after: dict[str, Any]) -> dict[str, Any]:
    return start_season(
        client,
        member_names=["Test Organizer", "Bob"],
        capacity=2,
        game_dates=[_in_days(30)],
        club_id=after["club_id"],
    )


def _organizer_id(client: TestClient, season: dict[str, Any]) -> int:
    members = client.get(f"/seasons/{season['id']}").json()["members"]
    return int(next(m["id"] for m in members if m["name"] == "Test Organizer"))


def _row(client: TestClient, season: dict[str, Any], player_id: int) -> dict[str, Any]:
    rows = client.get(
        f"/clubs/{season['club_id']}/balances", params={"season_id": season["id"]}
    ).json()
    row: dict[str, Any] = next(r for r in rows if r["player_id"] == player_id)
    return row


def test_the_fee_notice_states_the_fee_last_seasons_credit_and_what_is_due(
    client: TestClient, sent_messages: SentMessages
) -> None:
    autumn = _settled_season(client)
    me = _organizer_id(client, autumn)
    # Paid 100 more than autumn asked for, and kept it for next season.
    owed = -int(_row(client, autumn, me)["through_season"])
    client.post(
        f"/clubs/{autumn['club_id']}/players/{me}/payments",
        json={"amount": str(owed + 100), "season_id": autumn["id"]},
    )
    winter = _next_season(client, autumn)
    fee = -int(_row(client, winter, me)["season_fee_charged"])

    response = client.post(f"/seasons/{winter['id']}/fee-notice")

    assert response.status_code == 200, response.text
    assert response.json() == {"sent": 1, "unreachable": 0}
    [(user_id, text)] = sent_messages
    assert user_id == autumn["organizer_token"]
    assert f"本季季費：${fee}" in text
    assert "上季餘額：−$100" in text
    assert f"應繳金額：${fee - 100}" in text


def test_the_preview_is_word_for_word_what_gets_sent(
    client: TestClient, sent_messages: SentMessages
) -> None:
    # The confirmation sheet shows this, and a LINE message cannot be
    # taken back — so it is the server's text, not a copy of it.
    winter = _next_season(client, _settled_season(client))

    preview = client.get(f"/seasons/{winter['id']}/fee-notice").json()
    client.post(f"/seasons/{winter['id']}/fee-notice")

    assert [r["name"] for r in preview["recipients"]] == ["Test Organizer"]
    assert [r["text"] for r in preview["recipients"]] == [t for _u, t in sent_messages]


def test_the_refund_last_season_kept_is_named_with_how_many_absences(
    client: TestClient, sent_messages: SentMessages
) -> None:
    autumn = start_season(
        client,
        member_names=["Test Organizer", "Bob"],
        capacity=2,
        game_dates=["2026-08-18", "2026-08-25"],
    )
    # One night off, filled: a refund smaller than winter's fee.
    first_night = autumn["games"][0]["id"]
    client.post(
        "/absences", json={"player_name": "Test Organizer", "game_id": first_night}
    )
    client.post("/drop-ins", json={"player_name": "代打", "game_id": first_night})
    # Paid up front, so the refund is a credit kept for winter.
    me = _organizer_id(client, autumn)
    fee = -int(_row(client, autumn, me)["season_fee_charged"])
    client.post(
        f"/clubs/{autumn['club_id']}/players/{me}/payments",
        json={"amount": str(fee), "season_id": autumn["id"]},
    )
    settled = client.post(f"/seasons/{autumn['id']}/settle", json={}).json()
    refund = next(
        m["refund"] for m in settled["members"] if m["player_name"] == "Test Organizer"
    )
    winter = _next_season(client, autumn)

    [recipient] = client.get(f"/seasons/{winter['id']}/fee-notice").json()["recipients"]

    assert f"上季請假退費：−${refund}（1 次）" in recipient["text"]


def test_each_member_gets_the_fee_notice_once_a_season(
    client: TestClient, sent_messages: SentMessages
) -> None:
    # A LINE message cannot be taken back. The second tap finds nobody.
    winter = _next_season(client, _settled_season(client))

    client.post(f"/seasons/{winter['id']}/fee-notice")
    again = client.post(f"/seasons/{winter['id']}/fee-notice")

    assert again.json() == {"sent": 0, "unreachable": 0}
    assert len(sent_messages) == 1


def test_somebody_who_has_already_paid_is_not_asked_to(
    client: TestClient, sent_messages: SentMessages
) -> None:
    winter = _next_season(client, _settled_season(client))
    me = _organizer_id(client, winter)
    due = -int(_row(client, winter, me)["through_season"])
    client.post(
        f"/clubs/{winter['club_id']}/players/{me}/payments",
        json={"amount": str(due), "season_id": winter["id"]},
    )

    client.post(f"/seasons/{winter['id']}/fee-notice")

    assert sent_messages == []


def test_the_fee_notice_waits_for_the_previous_season_to_be_settled(
    client: TestClient, sent_messages: SentMessages
) -> None:
    autumn = start_season(
        client, member_names=["Test Organizer"], game_dates=["2026-08-18"]
    )
    winter = _next_season(client, autumn)

    response = client.post(f"/seasons/{winter['id']}/fee-notice")

    assert response.status_code == 400
    assert response.json()["detail"] == "The previous season is not settled"
    assert sent_messages == []
    detail = client.get(f"/seasons/{winter['id']}").json()
    assert detail["previous_season_settled"] is False


def test_a_season_already_over_has_no_fee_notice(client: TestClient) -> None:
    season = start_season(
        client, member_names=["Test Organizer"], game_dates=["2026-08-18"]
    )

    response = client.post(f"/seasons/{season['id']}/fee-notice")

    assert response.status_code == 400
    assert response.json()["detail"] == "Season has ended"


def test_somebody_line_refused_can_be_sent_it_later(
    client: TestClient, monkeypatch: Any, sent_messages: SentMessages
) -> None:
    # Not having added the Official Account is the usual reason. They are
    # left unmarked, so the next send reaches them — and only them.
    from volleyflow.notify import reminders

    winter = _next_season(client, _settled_season(client))

    def refuse(user_id: str, text: str) -> None:
        raise RuntimeError("LINE said no")

    monkeypatch.setattr(reminders, "push_to_user", refuse)
    first = client.post(f"/seasons/{winter['id']}/fee-notice").json()
    monkeypatch.setattr(
        reminders, "push_to_user", lambda u, t: sent_messages.append((u, t))
    )
    second = client.post(f"/seasons/{winter['id']}/fee-notice").json()

    assert first == {"sent": 0, "unreachable": 1}
    assert second == {"sent": 1, "unreachable": 0}


def test_the_season_says_who_has_been_sent_the_fee_notice(client: TestClient) -> None:
    winter = _next_season(client, _settled_season(client))

    client.post(f"/seasons/{winter['id']}/fee-notice")

    members = client.get(f"/seasons/{winter['id']}").json()["members"]
    sent = {m["name"]: m["fee_notice_sent_at"] is not None for m in members}
    assert sent == {"Test Organizer": True, "Bob": False}


def test_only_the_organizer_may_send_the_fee_notice(
    client: TestClient, sent_messages: SentMessages
) -> None:
    winter = _next_season(client, _settled_season(client))
    outsider = identify(client, "Outsider")

    response = client.post(
        f"/seasons/{winter['id']}/fee-notice",
        headers=auth_headers(outsider["token"]),
    )

    assert response.status_code == 403
    assert sent_messages == []
