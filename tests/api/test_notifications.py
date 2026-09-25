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


def test_settling_a_season_tells_each_member_it_can_reach(
    client: TestClient, sent_messages: SentMessages
) -> None:
    # The organizer is a season member here on purpose: create_club gave
    # them a LINE identity, and "Bob" — typed in by the organizer — has
    # none, so the organizer is the only member a push can reach. Bob
    # being silently skipped is the correct behaviour, not a gap.
    season = start_season(
        client,
        member_names=["Test Organizer", "Bob"],
        capacity=2,
        game_dates=["2026-08-18"],
    )

    response = client.post(f"/seasons/{season['id']}/settle", json={})

    assert response.status_code == 200
    assert [user_id for user_id, _ in sent_messages] == [season["organizer_token"]]
    assert "已結算" in sent_messages[0][1]
