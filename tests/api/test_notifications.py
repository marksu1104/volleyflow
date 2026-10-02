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

from datetime import date, timedelta
from typing import Any

from fastapi.testclient import TestClient

from tests.api.factories import auth_headers, identify, join_club, start_season

SentMessages = list[tuple[str, str]]


def _in_days(days: int) -> str:
    """A date that many days from now, as the API writes one.

    Local parts, never a UTC moment: Taiwan is UTC+8, so going through
    UTC can move the day. Used only where a test needs a game that hasn't
    been played yet — see the roster-removal test for why a written-down
    date won't do there.
    """
    return (date.today() + timedelta(days=days)).isoformat()


def _queued_behind_a_full_game(
    client: TestClient, name: str = "Carol"
) -> tuple[dict[str, str | int], int, dict[str, str]]:
    """A game filled by its two members, with one identified player
    waiting behind them."""
    season = start_season(
        client, member_names=["Alice", "Bob"], capacity=2, game_dates=["2026-08-18"]
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


def test_an_absence_promoting_somebody_tells_the_person_it_promoted(
    client: TestClient, sent_messages: SentMessages
) -> None:
    season, game_id, carol = _queued_behind_a_full_game(client)

    client.post("/absences", json={"player_name": "Alice", "game_id": game_id})

    assert [user_id for user_id, _ in sent_messages] == [carol["token"]], (
        "the queue put Carol on court; nobody else needs telling"
    )
    assert "2026-08-18" in sent_messages[0][1], "which night the message is about"


def test_a_cancelled_drop_in_promoting_somebody_tells_them_too(
    client: TestClient, sent_messages: SentMessages
) -> None:
    # The second of the two automatic paths. Both promote through
    # _promote_from_waitlist, but each commits in its own route, and the
    # push has to sit after that commit in both.
    season = start_season(
        client, member_names=["Alice"], capacity=2, game_dates=["2026-08-18"]
    )
    game_id = season["games"][0]["id"]
    leaving = client.post(
        "/drop-ins", json={"player_name": "先報名的", "game_id": game_id}
    ).json()
    carol = identify(client, "Carol")
    join_club(client, season["club_id"], auth_headers(carol["token"]))
    queued = client.post(
        "/drop-ins",
        json={"player_name": "Carol", "game_id": game_id},
        headers=auth_headers(carol["token"]),
    ).json()
    assert queued["status"] == "waitlisted"

    # POST .../cancel, not DELETE: a signup is withdrawn, not erased —
    # the row is kept with cancelled_at set so the ledger still shows the
    # charge and its refund.
    cancelled = client.post(f"/drop-ins/{leaving['id']}/cancel")

    assert cancelled.status_code == 200, cancelled.text
    assert [user_id for user_id, _ in sent_messages] == [carol["token"]]


def test_the_organizer_promoting_by_hand_tells_that_person(
    client: TestClient, sent_messages: SentMessages
) -> None:
    # The queue's own order is overridden here, so the person has even
    # less reason to expect it than usual.
    _season, game_id, carol = _queued_behind_a_full_game(client)
    entry = client.get(f"/seasons/{_season['id']}").json()["games"][0][
        "waitlist_entries"
    ][0]

    client.post("/absences", json={"player_name": "Alice", "game_id": game_id})
    sent_messages.clear()
    dave = identify(client, "Dave")
    join_club(client, _season["club_id"], auth_headers(dave["token"]))
    daves_entry = client.post(
        "/drop-ins",
        json={"player_name": "Dave", "game_id": game_id},
        headers=auth_headers(dave["token"]),
    ).json()
    assert daves_entry["status"] == "waitlisted"
    carols_drop_in = client.get(f"/seasons/{_season['id']}").json()["games"][0][
        "confirmed_drop_ins"
    ][0]

    client.post(
        f"/waitlist/{daves_entry['id']}/promote",
        json={"replacing_drop_in_id": carols_drop_in["id"]},
    )

    assert [user_id for user_id, _ in sent_messages] == [dave["token"]], (
        "Dave was put on court; Carol coming off is not a promotion"
    )
    assert entry["player_name"] == "Carol"


def test_taking_somebody_off_the_roster_tells_whoever_the_queue_promoted(
    client: TestClient, sent_messages: SentMessages
) -> None:
    """The one path that can promote several people into several
    different nights at once — which is why the notifier takes
    (game_id, player_id) pairs rather than one game and a list.
    """
    # Future dates, and computed rather than written down.
    # _offer_freed_slots_to_the_queue skips games already played on
    # purpose — promoting somebody into last month's game would put them
    # on a roster they never stood on — so this is the one test in this
    # file that cannot use the fixed 2026-08 dates the others do. A
    # hard-coded future date would only postpone the problem: it becomes
    # a past date eventually, and this test would start failing for a
    # reason that has nothing to do with the code.
    first, second = _in_days(7), _in_days(14)
    season = start_season(
        client,
        member_names=["Alice", "Bob"],
        capacity=2,
        game_dates=[first, second],
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
    members = client.get(f"/seasons/{season['id']}").json()["members"]
    alice = next(m for m in members if m["name"] == "Alice")

    # Asserted, not assumed: without this a refused request and a working
    # one that simply sent nothing look identical, and the failure names
    # the wrong cause.
    removed = client.delete(f"/seasons/{season['id']}/members/{alice['id']}")

    assert removed.status_code == 204, removed.text
    dates = sorted(text for _user_id, text in sent_messages)
    assert len(dates) == 2, "Alice's place opened at both games"
    assert first in dates[0]
    assert second in dates[1]


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
    assert f"季費 ${fee}" in text
    assert "上季餘額扣除 $100" in text
    assert f"應繳 ${fee - 100}" in text


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
