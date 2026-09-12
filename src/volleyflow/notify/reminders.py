"""Pre-game reminders and short-roster alerts.

See CLAUDE.md 2.3: "Games are auto-reminded before kickoff with the
current roster. If the roster is short, only the organizer is notified —
never the waitlist."
"""

import os
from datetime import date, timedelta

from sqlalchemy.orm import Session

from volleyflow.db.engine import get_session
from volleyflow.db.models import (
    AbsenceRow,
    DropInRow,
    GameRow,
    PlayerRow,
    SeasonMemberRow,
    SeasonRow,
)
from volleyflow.notify.line_client import push_to_user
from volleyflow.schedule import GameStatus


def _expected_roster(session: Session, game: GameRow, season: SeasonRow) -> list[str]:
    """Names expected to attend: fixed members minus this game's
    absences, plus confirmed (non-cancelled) drop-ins.
    """
    absent_ids = {
        row.player_id
        for row in session.query(AbsenceRow).filter(AbsenceRow.game_id == game.id).all()
    }
    member_rows = (
        session.query(PlayerRow)
        .join(SeasonMemberRow, SeasonMemberRow.player_id == PlayerRow.id)
        .filter(SeasonMemberRow.season_id == season.id)
        .all()
    )
    attending_members = [p.name for p in member_rows if p.id not in absent_ids]

    drop_in_rows = (
        session.query(PlayerRow)
        .join(DropInRow, DropInRow.player_id == PlayerRow.id)
        .filter(DropInRow.game_id == game.id, DropInRow.cancelled_at.is_(None))
        .all()
    )
    return attending_members + [p.name for p in drop_in_rows]


def send_game_reminder(session: Session, game: GameRow) -> None:
    """The short-roster alert, to the organizer alone.

    There is deliberately no message to the group chat. One was built —
    the roster and the price, the night before — and the organizer asked
    for it to be dropped: the group already talks about the game in the
    group, and a bot repeating the roster into that conversation is noise
    rather than news. The only thing worth interrupting anyone for is the
    thing nobody would otherwise notice in time, which is a game that
    doesn't have enough people yet, and that is one person's problem to
    solve.

    So this reads a roster it never announces. That asymmetry is the
    point: counting who is coming is what decides whether to say
    anything at all.
    """
    season = session.get(SeasonRow, game.season_id)
    assert season is not None  # game.season_id is a foreign key, always valid

    roster = _expected_roster(session, game, season)

    if len(roster) < season.minimum_roster:
        organizer_id = os.environ["LINE_ORGANIZER_USER_ID"]
        push_to_user(
            organizer_id,
            f"注意：{game.date} 這場人數不足，目前只有 {len(roster)} 人"
            f"（門檻 {season.minimum_roster} 人）",
        )


def send_reminders_for_date(session: Session, target_date: date) -> int:
    """Sends reminders for every scheduled game on `target_date`.

    Returns how many games were processed.
    """
    games = (
        session.query(GameRow)
        .filter(GameRow.date == target_date, GameRow.status == GameStatus.SCHEDULED)
        .all()
    )
    for game in games:
        send_game_reminder(session, game)
    return len(games)


if __name__ == "__main__":
    with get_session() as db_session:
        sent_count = send_reminders_for_date(
            db_session, date.today() + timedelta(days=1)
        )
    print(f"Sent reminders for {sent_count} game(s)")
