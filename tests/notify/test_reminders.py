"""Tests for pre-game reminders and short-roster alerts.

push_to_group/push_to_user are monkeypatched so nothing here ever hits
the real LINE API — see the sent_messages fixture.
"""

from datetime import date, datetime
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from volleyflow.db.models import (
    AbsenceRow,
    DropInRow,
    GameRow,
    PlayerRow,
    SeasonMemberRow,
    SeasonRow,
)
from volleyflow.notify import reminders

SentMessages = dict[str, list[tuple[str, str]]]


@pytest.fixture
def sent_messages(monkeypatch: pytest.MonkeyPatch) -> SentMessages:
    sent: SentMessages = {"group": [], "user": []}

    def fake_push_to_group(group_id: str, text: str) -> None:
        sent["group"].append((group_id, text))

    def fake_push_to_user(user_id: str, text: str) -> None:
        sent["user"].append((user_id, text))

    monkeypatch.setattr(reminders, "push_to_group", fake_push_to_group)
    monkeypatch.setattr(reminders, "push_to_user", fake_push_to_user)
    return sent


def _season(db_session: Session, minimum_roster: int = 2) -> SeasonRow:
    season = SeasonRow(
        total_venue_cost=Decimal("1000"), capacity=18, minimum_roster=minimum_roster
    )
    db_session.add(season)
    db_session.flush()
    return season


def test_reminder_sends_roster_to_the_group(
    db_session: Session, sent_messages: SentMessages, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LINE_GROUP_ID", "Cgroup123")
    monkeypatch.setenv("LINE_ORGANIZER_USER_ID", "Uorganizer")
    season = _season(db_session)
    alice = PlayerRow(name="Alice")
    db_session.add(alice)
    db_session.flush()
    db_session.add(SeasonMemberRow(season_id=season.id, player_id=alice.id))
    game = GameRow(season_id=season.id, date=date(2026, 8, 25))
    db_session.add(game)
    db_session.flush()

    reminders.send_game_reminder(db_session, game)

    assert len(sent_messages["group"]) == 1
    group_id, text = sent_messages["group"][0]
    assert group_id == "Cgroup123"
    assert "Alice" in text


def test_reminder_excludes_absent_members_and_includes_drop_ins(
    db_session: Session, sent_messages: SentMessages, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LINE_GROUP_ID", "Cgroup123")
    monkeypatch.setenv("LINE_ORGANIZER_USER_ID", "Uorganizer")
    season = _season(db_session, minimum_roster=1)
    alice = PlayerRow(name="Alice")
    carol = PlayerRow(name="Carol")
    db_session.add_all([alice, carol])
    db_session.flush()
    db_session.add(SeasonMemberRow(season_id=season.id, player_id=alice.id))
    game = GameRow(season_id=season.id, date=date(2026, 8, 25))
    db_session.add(game)
    db_session.flush()
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

    _, text = sent_messages["group"][0]
    assert "Alice" not in text
    assert "Carol" in text


def test_short_roster_alerts_only_the_organizer(
    db_session: Session, sent_messages: SentMessages, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("LINE_GROUP_ID", raising=False)
    monkeypatch.setenv("LINE_ORGANIZER_USER_ID", "Uorganizer")
    season = _season(db_session, minimum_roster=5)
    alice = PlayerRow(name="Alice")
    db_session.add(alice)
    db_session.flush()
    db_session.add(SeasonMemberRow(season_id=season.id, player_id=alice.id))
    game = GameRow(season_id=season.id, date=date(2026, 8, 25))
    db_session.add(game)
    db_session.flush()

    reminders.send_game_reminder(db_session, game)

    assert sent_messages["group"] == []
    assert len(sent_messages["user"]) == 1
    user_id, text = sent_messages["user"][0]
    assert user_id == "Uorganizer"
    assert "人數不足" in text


def test_roster_above_minimum_does_not_alert_organizer(
    db_session: Session, sent_messages: SentMessages, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LINE_GROUP_ID", "Cgroup123")
    monkeypatch.setenv("LINE_ORGANIZER_USER_ID", "Uorganizer")
    season = _season(db_session, minimum_roster=1)
    alice = PlayerRow(name="Alice")
    db_session.add(alice)
    db_session.flush()
    db_session.add(SeasonMemberRow(season_id=season.id, player_id=alice.id))
    game = GameRow(season_id=season.id, date=date(2026, 8, 25))
    db_session.add(game)
    db_session.flush()

    reminders.send_game_reminder(db_session, game)

    assert sent_messages["user"] == []


def test_send_reminders_for_date_only_processes_scheduled_games_that_day(
    db_session: Session, sent_messages: SentMessages, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LINE_GROUP_ID", "Cgroup123")
    monkeypatch.setenv("LINE_ORGANIZER_USER_ID", "Uorganizer")
    season = _season(db_session)
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
    assert len(sent_messages["group"]) == 2
