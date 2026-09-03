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
from volleyflow.notify.line_client import push_to_group, push_to_user
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
    season = session.get(SeasonRow, game.season_id)
    assert season is not None  # game.season_id is a foreign key, always valid

    roster = _expected_roster(session, game, season)

    group_id = os.environ.get("LINE_GROUP_ID")
    if group_id:
        time_range = ""
        if season.game_start_time and season.game_end_time:
            time_range = (
                f"（{season.game_start_time.strftime('%H:%M')}"
                f"-{season.game_end_time.strftime('%H:%M')}）"
            )
        roster_text = "、".join(roster) if roster else "目前沒有人"
        message = (
            f"{game.date} 球局提醒{time_range}\n"
            f"預計出席（{len(roster)} 人）：{roster_text}"
        )
        push_to_group(group_id, message)

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
