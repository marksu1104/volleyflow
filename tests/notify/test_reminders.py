"""Tests for the messages the app sends.

There is no group message — the organizer asked for it to be dropped.
The roster status goes to the club's organizers at each game's change
deadline (2026-10-05, replacing a check at 9am for short games), and the
promotion notices wait for that deadline too.

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
    LedgerEntryRow,
    PlayerRow,
    SeasonMemberRow,
    SeasonRow,
    WaitlistEntryRow,
)
from volleyflow.ledger import EntryType
from volleyflow.notify import reminders
from volleyflow.schedule import GameStatus

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


def _member(db_session: Session, season: SeasonRow, name: str) -> PlayerRow:
    person = PlayerRow(name=name)
    db_session.add(person)
    db_session.flush()
    db_session.add(SeasonMemberRow(season_id=season.id, player_id=person.id))
    db_session.flush()
    return person


def test_the_roster_status_goes_to_that_clubs_organizer(
    db_session: Session, sent_messages: SentMessages
) -> None:
    season = _season(db_session, minimum_roster=5, club_name="晴光館")
    game = _short_game(db_session, season)

    reminders.send_roster_status(db_session, game)

    assert [user_id for user_id, _ in sent_messages] == ["Uorganizer"]
    assert sent_messages[0][1].startswith("【晴光館】名單確定"), (
        "one person can run two clubs; the date alone won't say which"
    )


def test_another_clubs_organizer_is_never_told(
    db_session: Session, sent_messages: SentMessages
) -> None:
    # A stranger's game dates and headcount are not theirs.
    one = _season(db_session, club_name="A", organizer_line_id="Ua")
    _season(db_session, club_name="B", organizer_line_id="Ub")
    game = _short_game(db_session, one)

    reminders.send_roster_status(db_session, game)

    assert [user_id for user_id, _ in sent_messages] == ["Ua"]


def test_the_old_single_organizer_setting_no_longer_decides(
    db_session: Session, sent_messages: SentMessages, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LINE_ORGANIZER_USER_ID", "Udeveloper")
    season = _season(db_session, organizer_line_id="Uorganizer")
    game = _short_game(db_session, season)

    reminders.send_roster_status(db_session, game)

    assert [user_id for user_id, _ in sent_messages] == ["Uorganizer"]


def test_every_organizer_of_the_club_is_told(
    db_session: Session, sent_messages: SentMessages
) -> None:
    season = _season(db_session, organizer_line_id="Ufirst")
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

    reminders.send_roster_status(db_session, game)

    assert sorted(user_id for user_id, _ in sent_messages) == ["Ufirst", "Usecond"]


def test_an_ordinary_member_is_not_sent_the_roster_status(
    db_session: Session, sent_messages: SentMessages
) -> None:
    # Organizers only, at the organizer's choice (2026-10-05).
    season = _season(db_session)
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

    reminders.send_roster_status(db_session, game)

    assert "Umember" not in [user_id for user_id, _ in sent_messages]


def test_an_organizer_without_line_is_skipped_rather_than_crashing(
    db_session: Session, sent_messages: SentMessages
) -> None:
    season = _season(db_session, organizer_line_id=None)
    game = _short_game(db_session, season)

    reminders.send_roster_status(db_session, game)

    assert sent_messages == []


def test_the_roster_status_counts_who_is_playing_away_and_queued(
    db_session: Session, sent_messages: SentMessages
) -> None:
    """Members minus live absences plus drop-ins. A cancelled absence is
    not an absence: it used to be counted as one."""
    season = _season(db_session, minimum_roster=3, club_name="晴光館")
    alice = _member(db_session, season, "Alice")
    bob = _member(db_session, season, "Bob")
    _member(db_session, season, "Dave")
    carol = PlayerRow(name="Carol")
    eve = PlayerRow(name="Eve")
    db_session.add_all([carol, eve])
    db_session.flush()
    game = _short_game(db_session, season)
    db_session.add_all(
        [
            AbsenceRow(
                player_id=alice.id, game_id=game.id, recorded_at=datetime(2026, 8, 1)
            ),
            AbsenceRow(
                player_id=bob.id,
                game_id=game.id,
                recorded_at=datetime(2026, 8, 1),
                cancelled_at=datetime(2026, 8, 2),
            ),
            DropInRow(
                player_id=carol.id, game_id=game.id, signed_up_at=datetime(2026, 8, 1)
            ),
            WaitlistEntryRow(
                player_id=eve.id, game_id=game.id, queued_at=datetime(2026, 8, 1)
            ),
        ]
    )
    db_session.flush()

    reminders.send_roster_status(db_session, game)

    assert sent_messages[0][1].splitlines() == [
        "【晴光館】名單確定",
        "",
        "場次：8/25（二）",
        "",
        "名單：3／18 人",
        "（尚缺 15 人）",
        "",
        "請假：1 人",
        "候補：1 人",
        "",
        "場次與報名：",
        "https://liff.line.me/2011156233-6CouG6VI",
    ]


def test_a_full_game_says_so_and_a_short_one_is_flagged(
    db_session: Session, sent_messages: SentMessages
) -> None:
    full = _season(db_session, club_name="滿")
    full.capacity = 1
    full.minimum_roster = 1
    _member(db_session, full, "Alice")
    short = _season(
        db_session, minimum_roster=5, club_name="少", organizer_line_id="Ushort"
    )

    reminders.send_roster_status(db_session, _short_game(db_session, full))
    reminders.send_roster_status(db_session, _short_game(db_session, short))

    by_user = dict(sent_messages)
    assert "名單：1／1 人\n（已滿）" in by_user["Uorganizer"]
    assert "低於最低人數" not in by_user["Uorganizer"]
    assert "（低於最低人數 5 人）" in by_user["Ushort"]


def test_nothing_is_sent_to_a_group_even_if_one_is_configured(
    db_session: Session, sent_messages: SentMessages, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A leftover LINE_GROUP_ID must not resurrect the message the
    organizer asked to be rid of."""
    monkeypatch.setenv("LINE_GROUP_ID", "Cgroup123")
    assert not hasattr(reminders, "push_to_group")
    season = _season(db_session)
    game = _short_game(db_session, season)

    reminders.send_roster_status(db_session, game)

    assert [user_id for user_id, _ in sent_messages] == ["Uorganizer"]


# --- the deadline job -------------------------------------------------------

_GAME_DAY = date(2026, 8, 25)


def _at(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime.combine(day, time(hour, minute))


def _evening_game(db_session: Session, season: SeasonRow) -> GameRow:
    """20:00 on the 25th, with the default 24-hour deadline: it passes
    at 20:00 on the 24th."""
    season.game_start_time = time(20, 0)
    season.change_deadline_hours = 24
    return _short_game(db_session, season)


def test_nothing_goes_out_before_the_deadline(
    db_session: Session, sent_messages: SentMessages
) -> None:
    season = _season(db_session)
    game = _evening_game(db_session, season)

    sent = reminders.send_deadline_notices(db_session, _at(date(2026, 8, 24), 19, 50))

    assert sent == 0
    assert sent_messages == []
    assert game.roster_notice_sent_at is None


def test_once_past_the_deadline_the_notices_go_out_exactly_once(
    db_session: Session, sent_messages: SentMessages
) -> None:
    # The job runs every ten minutes; the second run must find it done.
    season = _season(db_session)
    game = _evening_game(db_session, season)

    first = reminders.send_deadline_notices(db_session, _at(date(2026, 8, 24), 20, 5))
    second = reminders.send_deadline_notices(db_session, _at(date(2026, 8, 24), 20, 15))

    assert (first, second) == (1, 0)
    assert len(sent_messages) == 1
    assert game.roster_notice_sent_at == _at(date(2026, 8, 24), 20, 5)


def test_a_game_already_played_or_called_off_is_left_alone(
    db_session: Session, sent_messages: SentMessages
) -> None:
    season = _season(db_session)
    played = _evening_game(db_session, season)
    called_off = GameRow(
        season_id=season.id,
        date=date(2026, 8, 30),
        status=GameStatus.CANCELLED_UNREFUNDED,
    )
    db_session.add(called_off)
    db_session.flush()

    reminders.send_deadline_notices(db_session, _at(date(2026, 8, 30), 21, 0))

    assert sent_messages == []
    assert played.roster_notice_sent_at is None


def test_one_unreachable_organizer_does_not_stop_the_other_games(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    delivered: list[str] = []

    def push(user_id: str, text: str) -> None:
        if user_id == "Unot-a-friend":
            raise RuntimeError("400 from LINE: not a friend")
        delivered.append(user_id)

    monkeypatch.setattr(reminders, "push_to_user", push)
    _evening_game(
        db_session,
        _season(db_session, club_name="A", organizer_line_id="Unot-a-friend"),
    )
    _evening_game(
        db_session, _season(db_session, club_name="B", organizer_line_id="Ureachable")
    )

    sent = reminders.send_deadline_notices(db_session, _at(date(2026, 8, 24), 21, 0))

    assert sent == 2
    assert delivered == ["Ureachable"]


def test_at_the_deadline_everyone_promoted_hears_and_so_does_who_signed_them_up(
    db_session: Session, sent_messages: SentMessages
) -> None:
    season = _season(db_session, club_name="晴光館")
    game = _evening_game(db_session, season)
    bringer = PlayerRow(name="謝秉均", line_user_id="Ubringer")
    guest = PlayerRow(name="C")  # no LINE account, like most guests
    db_session.add_all([bringer, guest])
    db_session.flush()
    db_session.add(
        DropInRow(
            player_id=guest.id,
            game_id=game.id,
            signed_up_at=datetime(2026, 8, 20),
            from_waitlist_at=datetime(2026, 8, 20),
            brought_by_player_id=bringer.id,
        )
    )
    db_session.flush()

    reminders.send_deadline_notices(db_session, _at(date(2026, 8, 24), 20, 5))

    # The same words as the person themselves gets; who signed them up
    # is not the news (2026-10-05).
    to_bringer = dict(sent_messages)["Ubringer"]
    assert to_bringer.splitlines()[:5] == [
        "【晴光館】遞補通知",
        "",
        "場次：8/25（二）20:00",
        "",
        "遞補上場：C",
    ]


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
    assert "待核准：2 人" in text


def test_nobody_waiting_to_join_sends_nothing(
    db_session: Session, sent_messages: SentMessages
) -> None:
    _season(db_session)

    clubs = reminders.send_join_request_digests(db_session)

    assert clubs == 0
    assert sent_messages == []


def _charge(db_session: Session, season: SeasonRow, player: PlayerRow, amount: str):
    """A ledger entry, so the notice has a balance to report. Signed from
    the player's side: negative means they owe the club."""
    db_session.add(
        LedgerEntryRow(
            player_id=player.id,
            club_id=season.club_id,
            entry_type=EntryType.SEASON_FEE_CHARGED,
            amount=Decimal(amount),
            recorded_at=datetime.now(),
            season_id=season.id,
        )
    )
    db_session.flush()


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

    game.roster_notice_sent_at = datetime.now()
    reminders.send_roster_status(db_session, game)
    reminders.send_join_request_digests(db_session)
    reminders.notify_promoted_from_waitlist(db_session, [(game.id, queued.id)])
    reminders.notify_fee_due(
        db_session,
        season,
        [reminders.FeeDue(member.id, Decimal(500), Decimal(0), Decimal(500))],
    )

    assert len(sent_messages) == 4, "名單確定、待核准、候補遞補、繳費通知"
    links = [text.splitlines()[-1] for _user_id, text in sent_messages]
    assert links[:3] == ["https://liff.line.me/2011156233-6CouG6VI"] * 3
    # The fee notice opens 我的帳務, on this club.
    assert links[3] == f"https://liff.line.me/2011156233-SWicoUre?club={season.club_id}"


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
    game.roster_notice_sent_at = datetime.now()
    queued = PlayerRow(name="候補的人", line_user_id="Uqueued")
    db_session.add(queued)
    db_session.flush()

    told = reminders.notify_promoted_from_waitlist(db_session, [(game.id, queued.id)])

    assert told == 1
    user_id, text = sent_messages[0]
    assert user_id == "Uqueued"
    assert "晴光館" in text
    assert "場次：8/25（二）" in text


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
    tuesday = GameRow(
        season_id=season.id,
        date=date(2026, 8, 25),
        roster_notice_sent_at=datetime.now(),
    )
    friday = GameRow(
        season_id=season.id,
        date=date(2026, 8, 28),
        roster_notice_sent_at=datetime.now(),
    )
    first = PlayerRow(name="週二的人", line_user_id="Utuesday")
    second = PlayerRow(name="週五的人", line_user_id="Ufriday")
    db_session.add_all([tuesday, friday, first, second])
    db_session.flush()

    told = reminders.notify_promoted_from_waitlist(
        db_session, [(tuesday.id, first.id), (friday.id, second.id)]
    )

    assert told == 2
    by_user = dict(sent_messages)
    assert "場次：8/25（二）" in by_user["Utuesday"]
    assert "場次：8/28（五）" in by_user["Ufriday"]


def test_one_unreachable_promoted_player_does_not_stop_the_others(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Same failure the organizer alert was fixed for on 2026-09-15: LINE
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
    game.roster_notice_sent_at = datetime.now()
    blocked = PlayerRow(name="封鎖的人", line_user_id="Unot-a-friend")
    fine = PlayerRow(name="正常的人", line_user_id="Ufine")
    db_session.add_all([blocked, fine])
    db_session.flush()

    told = reminders.notify_promoted_from_waitlist(
        db_session, [(game.id, blocked.id), (game.id, fine.id)]
    )

    assert told == 1
    assert delivered == ["Ufine"]


def test_a_promotion_before_the_deadline_waits_for_it(
    db_session: Session, sent_messages: SentMessages
) -> None:
    # A 代打 named an hour later could undo it; the batch at the deadline
    # tells whoever is promoted by then.
    season = _season(db_session)
    game = _short_game(db_session, season)
    queued = PlayerRow(name="候補的人", line_user_id="Uqueued")
    db_session.add(queued)
    db_session.flush()

    told = reminders.notify_promoted_from_waitlist(db_session, [(game.id, queued.id)])

    assert told == 0
    assert sent_messages == []


def test_a_cancelled_game_tells_everyone_expected_and_their_bringers(
    db_session: Session, sent_messages: SentMessages
) -> None:
    season = _season(db_session, club_name="晴光館")
    playing = PlayerRow(name="上場的", line_user_id="Uplaying")
    away = PlayerRow(name="請假的", line_user_id="Uaway")
    bringer = PlayerRow(name="帶人的", line_user_id="Ubringer")
    guest = PlayerRow(name="朋友")
    db_session.add_all([playing, away, bringer, guest])
    db_session.flush()
    db_session.add_all(
        [
            SeasonMemberRow(season_id=season.id, player_id=playing.id),
            SeasonMemberRow(season_id=season.id, player_id=away.id),
        ]
    )
    game = GameRow(
        season_id=season.id,
        date=date(2099, 8, 25),
        status=GameStatus.CANCELLED_REFUNDED,
    )
    db_session.add(game)
    db_session.flush()
    db_session.add_all(
        [
            AbsenceRow(
                player_id=away.id, game_id=game.id, recorded_at=datetime(2099, 8, 1)
            ),
            DropInRow(
                player_id=guest.id,
                game_id=game.id,
                signed_up_at=datetime(2099, 8, 1),
                brought_by_player_id=bringer.id,
            ),
        ]
    )
    db_session.flush()

    reminders.notify_game_cancelled(db_session, game)

    assert sorted(u for u, _ in sent_messages) == ["Ubringer", "Uplaying"]
    assert sent_messages[0][1].splitlines()[:6] == [
        "【晴光館】場次取消",
        "",
        "場次：8/25（二）",
        "",
        "本場已取消，",
        "本場費用已退還。",
    ]


def _due(earlier: str, refund: str = "0", absences: int = 0) -> reminders.FeeDue:
    return reminders.FeeDue(
        1,
        Decimal(4700),
        Decimal(earlier),
        Decimal(4700) - Decimal(earlier),
        previous_refund=Decimal(refund),
        previous_absences=absences,
    )


def test_the_fee_notice_states_the_season_the_refund_and_what_is_due(
    db_session: Session,
) -> None:
    season = _season(db_session, club_name="晴光館")
    for day in (7, 14):
        db_session.add(GameRow(season_id=season.id, date=date(2026, 10, day)))
    db_session.flush()

    text = reminders.fee_notice_text(db_session, season, _due("470", "470", 2))

    assert text.splitlines() == [
        "【晴光館】繳費通知",
        "",
        "季別：10/7–10/14",
        "共 2 場",
        "",
        "本季季費：$4700",
        "上季退費：−$470",
        "（請假 2 次）",
        "",
        "應繳金額：$4230",
        "",
        "帳務明細：",
        f"https://liff.line.me/2011156233-SWicoUre?club={season.club_id}",
    ]


def test_last_season_unpaid_beside_the_refund_is_its_own_line() -> None:
    lines = reminders._earlier_lines(_due("-230", "470", 2))

    assert lines == ["上季退費：−$470", "（請假 2 次）", "上季未繳：+$700"]


def test_a_credit_that_is_not_a_refund_is_called_last_seasons_balance() -> None:
    assert reminders._earlier_lines(_due("100")) == ["上季餘額：−$100"]


def test_nothing_from_last_season_is_not_mentioned_at_all() -> None:
    # A refund already paid out in cash at settlement leaves nothing over,
    # so it isn't brought up again; "上季餘額：$0" reads like an error.
    assert reminders._earlier_lines(_due("0", "470", 2)) == []


def _width(line: str) -> float:
    """Roughly how wide a line sets in LINE: a Chinese character or
    full-width mark is one, a digit, letter or ASCII mark is half."""
    return sum(0.5 if ord(ch) < 0x2E80 and ch not in "−–›" else 1.0 for ch in line)


def test_no_line_in_any_notice_is_long_enough_to_wrap_on_a_small_phone(
    db_session: Session, sent_messages: SentMessages
) -> None:
    """About ten Chinese characters fit across a LINE bubble on a small
    phone (measured 2026-10-05). Every notice, every line, links aside —
    those cannot be shortened and always wrap, so they sit alone."""
    season = _season(db_session, minimum_roster=5, club_name="週二排球")
    season.game_start_time = time(20, 0)
    game = _short_game(db_session, season)
    queued = PlayerRow(name="候補的人", line_user_id="Uqueued")
    member = PlayerRow(name="成員", line_user_id="Umember")
    db_session.add_all([queued, member])
    db_session.flush()
    _waiting_to_join(db_session, season, "新人一")

    reminders.send_roster_status(db_session, game)
    reminders.send_join_request_digests(db_session)
    game.roster_notice_sent_at = datetime.now()
    reminders.notify_promoted_from_waitlist(db_session, [(game.id, queued.id)])
    reminders.notify_fee_due(
        db_session,
        season,
        [
            reminders.FeeDue(
                member.id,
                Decimal(4700),
                Decimal(-230),
                Decimal(4930),
                previous_refund=Decimal(470),
                previous_absences=2,
            )
        ],
    )
    game.status = GameStatus.CANCELLED_UNREFUNDED
    game.date = date(2099, 8, 25)
    db_session.add(SeasonMemberRow(season_id=season.id, player_id=member.id))
    db_session.flush()
    reminders.notify_game_cancelled(db_session, game)

    assert len(sent_messages) == 5
    too_long = [
        line
        for _user, text in sent_messages
        for line in text.splitlines()
        if not line.startswith("https://") and _width(line) > 11
    ]
    assert too_long == []
