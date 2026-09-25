"""Tests for the short-roster alert.

There is no group message any more — the organizer asked for it to be
dropped (see reminders.send_game_reminder). What is left reads the
roster only to decide whether anybody needs telling, and tells that
club's organizers when they do.

It used to tell one person, named by an environment variable, whatever
club the game was in — right while the app ran one club, wrong from the
day it became multi-tenant. These tests are mostly about who hears, and
who doesn't.

push_to_user is monkeypatched so nothing here ever hits the real LINE
API — see the sent_messages fixture.
"""

from datetime import date, datetime, time
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from volleyflow.db.models import (
    AbsenceRow,
    ClubMemberRow,
    ClubRow,
    DropInRow,
    GameRow,
    PlayerRow,
    SeasonMemberRow,
    SeasonRow,
)
from volleyflow.notify import reminders
from volleyflow.players import Player
from volleyflow.settlement import MemberSettlement

SentMessages = list[tuple[str, str]]


# The sent_messages fixture moved to tests/conftest.py on 2026-09-25, so
# it covers the whole suite rather than this file alone. It had to: the
# API routes send pushes of their own now (a waitlist promotion, a
# settled season), and those tests were reaching the real line_client,
# raising KeyError on the missing token inside the notifier's own
# try/except, and passing anyway. Two autouse fixtures patching the same
# attribute would also have made which one wins depend on ordering, and
# the losing one's list is silently never written to.


def _season(
    db_session: Session,
    minimum_roster: int = 2,
    game_start_time: time | None = None,
    game_end_time: time | None = None,
    club_name: str = "Test Club",
    organizer_line_id: str | None = "Uorganizer",
) -> SeasonRow:
    """A season in a club of its own, run by one organizer.

    Flushed parent-first: models.py declares no relationship(), so the
    ORM would otherwise insert club_members before the club and player it
    points at (see tests/conftest.py).
    """
    club = ClubRow(name=club_name, created_at=datetime.now())
    organizer = PlayerRow(name=f"{club_name} organizer", line_user_id=organizer_line_id)
    db_session.add_all([club, organizer])
    db_session.flush()
    db_session.add(
        ClubMemberRow(
            club_id=club.id,
            player_id=organizer.id,
            role="organizer",
            joined_at=datetime.now(),
        )
    )
    season = SeasonRow(
        club_id=club.id,
        total_venue_cost=Decimal("1000"),
        capacity=18,
        minimum_roster=minimum_roster,
        game_start_time=game_start_time,
        game_end_time=game_end_time,
    )
    db_session.add(season)
    db_session.flush()
    return season


def _short_game(db_session: Session, season: SeasonRow) -> GameRow:
    """A game with nobody on it, which is short for any minimum above 0."""
    game = GameRow(season_id=season.id, date=date(2026, 8, 25))
    db_session.add(game)
    db_session.flush()
    return game


def test_a_short_roster_alerts_that_clubs_organizer(
    db_session: Session, sent_messages: SentMessages
) -> None:
    season = _season(db_session, minimum_roster=5, club_name="晴光館")
    alice = PlayerRow(name="Alice")
    db_session.add(alice)
    db_session.flush()
    db_session.add(SeasonMemberRow(season_id=season.id, player_id=alice.id))
    game = GameRow(season_id=season.id, date=date(2026, 8, 25))
    db_session.add(game)
    db_session.flush()

    reminders.send_game_reminder(db_session, game)

    assert len(sent_messages) == 1
    user_id, text = sent_messages[0]
    assert user_id == "Uorganizer"
    assert "人數不足" in text
    assert "晴光館" in text, (
        "one person can run two clubs; the date alone won't say which"
    )


def test_another_clubs_organizer_is_never_told(
    db_session: Session, sent_messages: SentMessages
) -> None:
    # The multi-tenant bug itself: an alert about one club reached
    # whoever the single configured id belonged to, whatever club the
    # game was in. A stranger's game dates and headcount are not theirs.
    short = _season(db_session, minimum_roster=5, club_name="A", organizer_line_id="Ua")
    _season(db_session, minimum_roster=5, club_name="B", organizer_line_id="Ub")
    game = _short_game(db_session, short)

    reminders.send_game_reminder(db_session, game)

    assert [user_id for user_id, _ in sent_messages] == ["Ua"]


def test_the_old_single_organizer_setting_no_longer_decides(
    db_session: Session, sent_messages: SentMessages, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Still set in the environment today, and must not be what anyone's
    # alert is addressed to.
    monkeypatch.setenv("LINE_ORGANIZER_USER_ID", "Udeveloper")
    season = _season(db_session, minimum_roster=5, organizer_line_id="Uorganizer")
    game = _short_game(db_session, season)

    reminders.send_game_reminder(db_session, game)

    assert [user_id for user_id, _ in sent_messages] == ["Uorganizer"]


def test_every_organizer_of_the_club_is_told(
    db_session: Session, sent_messages: SentMessages
) -> None:
    season = _season(db_session, minimum_roster=5, organizer_line_id="Ufirst")
    second = PlayerRow(name="Second organizer", line_user_id="Usecond")
    db_session.add(second)
    db_session.flush()
    db_session.add(
        ClubMemberRow(
            club_id=season.club_id,
            player_id=second.id,
            role="organizer",
            joined_at=datetime.now(),
        )
    )
    game = _short_game(db_session, season)

    reminders.send_game_reminder(db_session, game)

    assert sorted(user_id for user_id, _ in sent_messages) == ["Ufirst", "Usecond"]


def test_an_ordinary_member_is_not_told(
    db_session: Session, sent_messages: SentMessages
) -> None:
    # The attendance rules: only the organizer is notified.
    season = _season(db_session, minimum_roster=5)
    member = PlayerRow(name="Member", line_user_id="Umember")
    db_session.add(member)
    db_session.flush()
    db_session.add(
        ClubMemberRow(
            club_id=season.club_id,
            player_id=member.id,
            role="member",
            joined_at=datetime.now(),
        )
    )
    game = _short_game(db_session, season)

    reminders.send_game_reminder(db_session, game)

    assert "Umember" not in [user_id for user_id, _ in sent_messages]


def test_an_organizer_without_line_is_skipped_rather_than_crashing(
    db_session: Session, sent_messages: SentMessages
) -> None:
    season = _season(db_session, minimum_roster=5, organizer_line_id=None)
    game = _short_game(db_session, season)

    reminders.send_game_reminder(db_session, game)

    assert sent_messages == []


def test_one_unreachable_organizer_does_not_stop_the_rest_of_the_night(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """LINE refuses a push to anybody who hasn't added the Official
    Account as a friend, and the client raises on it. Before this, the
    first such organizer ended the whole run, and every club after them
    in the night's list went unalerted."""
    delivered: list[str] = []

    def push(user_id: str, text: str) -> None:
        if user_id == "Unot-a-friend":
            raise RuntimeError("400 from LINE: not a friend")
        delivered.append(user_id)

    monkeypatch.setattr(reminders, "push_to_user", push)
    first = _season(
        db_session, minimum_roster=5, club_name="A", organizer_line_id="Unot-a-friend"
    )
    second = _season(
        db_session, minimum_roster=5, club_name="B", organizer_line_id="Ureachable"
    )
    _short_game(db_session, first)
    _short_game(db_session, second)

    count = reminders.send_reminders_for_date(db_session, date(2026, 8, 25))

    assert count == 2
    assert delivered == ["Ureachable"]


def test_a_roster_above_the_minimum_says_nothing_at_all(
    db_session: Session, sent_messages: SentMessages
) -> None:
    # The whole point of dropping the group message: a game that is fine
    # is not news, and nobody hears from this at all.
    season = _season(db_session, minimum_roster=1)
    alice = PlayerRow(name="Alice")
    db_session.add(alice)
    db_session.flush()
    db_session.add(SeasonMemberRow(season_id=season.id, player_id=alice.id))
    game = GameRow(season_id=season.id, date=date(2026, 8, 25))
    db_session.add(game)
    db_session.flush()

    reminders.send_game_reminder(db_session, game)

    assert sent_messages == []


def test_the_count_excludes_absent_members_and_includes_drop_ins(
    db_session: Session, sent_messages: SentMessages
) -> None:
    """The roster arithmetic still decides whether to alert, even though
    the names themselves are no longer announced anywhere."""
    season = _season(db_session, minimum_roster=3)
    alice = PlayerRow(name="Alice")
    bob = PlayerRow(name="Bob")
    carol = PlayerRow(name="Carol")
    db_session.add_all([alice, bob, carol])
    db_session.flush()
    db_session.add(SeasonMemberRow(season_id=season.id, player_id=alice.id))
    db_session.add(SeasonMemberRow(season_id=season.id, player_id=bob.id))
    game = GameRow(season_id=season.id, date=date(2026, 8, 25))
    db_session.add(game)
    db_session.flush()
    # Alice is away, Carol drops in: two members minus one absence plus
    # one drop-in is two, which is below the minimum of three.
    db_session.add(
        AbsenceRow(
            player_id=alice.id, game_id=game.id, recorded_at=datetime(2026, 8, 1)
        )
    )
    db_session.add(
        DropInRow(
            player_id=carol.id, game_id=game.id, signed_up_at=datetime(2026, 8, 1)
        )
    )
    db_session.flush()

    reminders.send_game_reminder(db_session, game)

    assert len(sent_messages) == 1
    assert "只有 2 人" in sent_messages[0][1]


def test_nothing_is_sent_to_a_group_even_if_one_is_configured(
    db_session: Session, sent_messages: SentMessages, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A leftover LINE_GROUP_ID in some environment must not resurrect
    the message the organizer asked to be rid of. There is no code path
    left that could, and this is what says so out loud."""
    monkeypatch.setenv("LINE_GROUP_ID", "Cgroup123")
    assert not hasattr(reminders, "push_to_group")

    season = _season(db_session, minimum_roster=1)
    alice = PlayerRow(name="Alice")
    db_session.add(alice)
    db_session.flush()
    db_session.add(SeasonMemberRow(season_id=season.id, player_id=alice.id))
    game = GameRow(season_id=season.id, date=date(2026, 8, 25))
    db_session.add(game)
    db_session.flush()

    reminders.send_game_reminder(db_session, game)

    assert sent_messages == []


def test_send_reminders_for_date_only_processes_scheduled_games_that_day(
    db_session: Session, sent_messages: SentMessages
) -> None:
    season = _season(db_session, minimum_roster=5)
    db_session.add_all(
        [
            GameRow(season_id=season.id, date=date(2026, 8, 25)),
            GameRow(season_id=season.id, date=date(2026, 8, 25)),
            GameRow(season_id=season.id, date=date(2026, 9, 1)),  # different day
        ]
    )
    db_session.flush()

    count = reminders.send_reminders_for_date(db_session, date(2026, 8, 25))

    assert count == 2
    assert len(sent_messages) == 2, "both of that day's games are short-handed"


def _waiting_to_join(db_session: Session, season: SeasonRow, name: str) -> None:
    person = PlayerRow(name=name, line_user_id=f"U-{name}")
    db_session.add(person)
    db_session.flush()
    db_session.add(
        ClubMemberRow(
            club_id=season.club_id,
            player_id=person.id,
            role="member",
            joined_at=datetime.now(),
            status="pending",
        )
    )
    db_session.flush()


def test_people_waiting_to_join_are_one_message_to_the_organizer(
    db_session: Session, sent_messages: SentMessages
) -> None:
    season = _season(db_session, club_name="晴光館")
    _waiting_to_join(db_session, season, "新人一")
    _waiting_to_join(db_session, season, "新人二")

    clubs = reminders.send_join_request_digests(db_session)

    assert clubs == 1
    assert len(sent_messages) == 1
    user_id, text = sent_messages[0]
    assert user_id == "Uorganizer"
    assert "晴光館" in text
    assert "2 位" in text


def test_nobody_waiting_to_join_sends_nothing(
    db_session: Session, sent_messages: SentMessages
) -> None:
    _season(db_session)

    clubs = reminders.send_join_request_digests(db_session)

    assert clubs == 0
    assert sent_messages == []


def _settlement(player: PlayerRow, refund: str) -> MemberSettlement:
    return MemberSettlement(
        player=Player(id=player.id, name=player.name),
        season_fee=Decimal("500"),
        refund=Decimal(refund),
    )


def test_every_message_carries_a_way_back_into_the_app(
    db_session: Session, sent_messages: SentMessages
) -> None:
    """A notice that names a problem without offering a route to act on
    it sends the reader off to find the app themselves — and the join
    digest was worse, naming a screen it gave no way to reach.

    All four push paths in one test on purpose: a fifth message type
    added later without the link fails here rather than shipping quietly.
    Two of them were added on 2026-09-25 and this assertion had to grow
    with them — the promise in this docstring is only worth anything if
    somebody keeps it.

    The literal address, not `reminders.APP_URL`, which would only
    compare the constant to itself. This pins the real LIFF app, so
    changing it has to be deliberate.
    """
    season = _season(db_session, minimum_roster=5, club_name="晴光館")
    game = _short_game(db_session, season)
    _waiting_to_join(db_session, season, "新人一")
    queued = PlayerRow(name="候補的人", line_user_id="Uqueued")
    member = PlayerRow(name="季末的人", line_user_id="Usettled")
    db_session.add_all([queued, member])
    db_session.flush()

    reminders.send_game_reminder(db_session, game)
    reminders.send_join_request_digests(db_session)
    reminders.notify_promoted_from_waitlist(db_session, [(game.id, queued.id)])
    reminders.notify_season_settled(db_session, season, [_settlement(member, "100")])

    assert len(sent_messages) == 4, "短名單、待核准、候補遞補、季末結算"
    for _user_id, text in sent_messages:
        assert "https://liff.line.me/2011156233-6CouG6VI" in text


def test_a_promoted_player_is_told_which_night_is_theirs(
    db_session: Session, sent_messages: SentMessages
) -> None:
    """The date is the point. Somebody can be queued for several games at
    once, and "你遞補上了" without saying which night is a message they
    have to open the app to understand — which is what the notification
    exists to save them.
    """
    season = _season(db_session, club_name="晴光館")
    game = _short_game(db_session, season)
    queued = PlayerRow(name="候補的人", line_user_id="Uqueued")
    db_session.add(queued)
    db_session.flush()

    told = reminders.notify_promoted_from_waitlist(db_session, [(game.id, queued.id)])

    assert told == 1
    user_id, text = sent_messages[0]
    assert user_id == "Uqueued"
    assert "晴光館" in text
    assert "2026-08-25" in text


def test_a_promoted_guest_without_line_is_skipped_rather_than_crashing(
    db_session: Session, sent_messages: SentMessages
) -> None:
    # The ordinary case, not an edge one: a guest somebody typed in by
    # hand has no LINE account at all, and most drop-ins are exactly that.
    season = _season(db_session)
    game = _short_game(db_session, season)
    guest = PlayerRow(name="朋友的朋友")
    db_session.add(guest)
    db_session.flush()

    told = reminders.notify_promoted_from_waitlist(db_session, [(game.id, guest.id)])

    assert told == 0
    assert sent_messages == []


def test_one_removal_can_promote_people_into_different_nights(
    db_session: Session, sent_messages: SentMessages
) -> None:
    """Why this takes (game_id, player_id) pairs rather than a game and a
    list of people: taking one person off the roster frees their place at
    every game they were expected at, so a single action promotes several
    people into several different nights. Each has to hear their own date.
    """
    season = _season(db_session, club_name="晴光館")
    tuesday = GameRow(season_id=season.id, date=date(2026, 8, 25))
    friday = GameRow(season_id=season.id, date=date(2026, 8, 28))
    first = PlayerRow(name="週二的人", line_user_id="Utuesday")
    second = PlayerRow(name="週五的人", line_user_id="Ufriday")
    db_session.add_all([tuesday, friday, first, second])
    db_session.flush()

    told = reminders.notify_promoted_from_waitlist(
        db_session, [(tuesday.id, first.id), (friday.id, second.id)]
    )

    assert told == 2
    by_user = dict(sent_messages)
    assert "2026-08-25" in by_user["Utuesday"]
    assert "2026-08-28" in by_user["Ufriday"]


def test_one_unreachable_promoted_player_does_not_stop_the_others(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Same failure send_game_reminder was fixed for on 2026-09-15: LINE
    # raises for anybody who hasn't added the Official Account, and one
    # of them must not swallow everyone after them in the list.
    delivered: list[str] = []

    def push(user_id: str, text: str) -> None:
        if user_id == "Unot-a-friend":
            raise RuntimeError("400 from LINE: not a friend")
        delivered.append(user_id)

    monkeypatch.setattr(reminders, "push_to_user", push)
    season = _season(db_session)
    game = _short_game(db_session, season)
    blocked = PlayerRow(name="封鎖的人", line_user_id="Unot-a-friend")
    fine = PlayerRow(name="正常的人", line_user_id="Ufine")
    db_session.add_all([blocked, fine])
    db_session.flush()

    told = reminders.notify_promoted_from_waitlist(
        db_session, [(game.id, blocked.id), (game.id, fine.id)]
    )

    assert told == 1
    assert delivered == ["Ufine"]


def test_settling_tells_each_member_what_they_are_owed(
    db_session: Session, sent_messages: SentMessages
) -> None:
    """The amount is the only reason this message exists. "已結算" alone
    sends every member into the app to find one number.
    """
    season = _season(db_session, club_name="晴光館")
    member = PlayerRow(name="有退費的", line_user_id="Urefund")
    db_session.add(member)
    db_session.flush()

    told = reminders.notify_season_settled(
        db_session, season, [_settlement(member, "235")]
    )

    assert told == 1
    user_id, text = sent_messages[0]
    assert user_id == "Urefund"
    assert "晴光館" in text
    assert "$235" in text


def test_a_member_with_nothing_owed_is_not_told_about_zero(
    db_session: Session, sent_messages: SentMessages
) -> None:
    # "$0 退費" reads like something went wrong. Somebody who played every
    # night they paid for gets a plain notice instead.
    season = _season(db_session)
    member = PlayerRow(name="沒退費的", line_user_id="Unothing")
    db_session.add(member)
    db_session.flush()

    reminders.notify_season_settled(db_session, season, [_settlement(member, "0")])

    _user_id, text = sent_messages[0]
    assert "$0" not in text
    assert "沒有可退的費用" in text
