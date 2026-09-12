"""Tests for the short-roster alert.

There is no group message any more — the organizer asked for it to be
dropped (see reminders.send_game_reminder). What is left reads the
roster only to decide whether anybody needs telling, and tells one
person when they do.

push_to_user is monkeypatched so nothing here ever hits the real LINE
API — see the sent_messages fixture.
"""

from datetime import date, datetime, time
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from volleyflow.db.models import (
    AbsenceRow,
    ClubRow,
    DropInRow,
    GameRow,
    PlayerRow,
    SeasonMemberRow,
    SeasonRow,
)
from volleyflow.notify import reminders

SentMessages = list[tuple[str, str]]


@pytest.fixture(autouse=True)
def sent_messages(monkeypatch: pytest.MonkeyPatch) -> SentMessages:
    """Autouse, so no test in this file can reach the real LINE API even
    by forgetting to ask for the fixture."""
    sent: SentMessages = []

    def fake_push_to_user(user_id: str, text: str) -> None:
        sent.append((user_id, text))

    monkeypatch.setattr(reminders, "push_to_user", fake_push_to_user)
    monkeypatch.setenv("LINE_ORGANIZER_USER_ID", "Uorganizer")
    return sent


def _season(
    db_session: Session,
    minimum_roster: int = 2,
    game_start_time: time | None = None,
    game_end_time: time | None = None,
) -> SeasonRow:
    club = ClubRow(name="Test Club", created_at=datetime.now())
    db_session.add(club)
    db_session.flush()
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


def test_a_short_roster_alerts_the_organizer(
    db_session: Session, sent_messages: SentMessages
) -> None:
    season = _season(db_session, minimum_roster=5)
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
