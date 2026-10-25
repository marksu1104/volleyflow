"""Every LINE message the app sends, and when. All are private messages;
nothing goes to a group.

- At a game's change deadline, once: the roster status to that club's
  organizers, and a promotion notice (遞補通知) to everybody who came off
  the queue and to whoever signed them up. Held until the deadline
  because the roster keeps moving until then.
- After the deadline, every ten minutes: a promotion notice to whoever
  has come off the queue since, if still on court when the job runs,
  once per signup. A queued person named a 代打 counts as promoted.
- A game called off (場次取消): everybody expected at it.
- People waiting to join (加入申請): the club's organizers, once a day.
- The season's fee notice (繳費通知): each member, when the organizer
  sends it, at most once a season.
"""

import logging
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import func
from sqlalchemy.orm import Session

from volleyflow.db.engine import get_session
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
from volleyflow.notify.line_client import push_to_user
from volleyflow.schedule import GameStatus, change_deadline

logger = logging.getLogger("volleyflow.reminders")

# Every push ends with a link to the member app. A liff.line.me address,
# because only a LIFF URL opens inside LINE with the user signed in. No LIFF
# app points at the organizer pages, so notices for organizers name the
# screen instead of linking to it.
APP_URL = "https://liff.line.me/2011156233-6CouG6VI"
LEDGER_URL = "https://liff.line.me/2011156233-SWicoUre"
"""我的帳務 (ledger.html), a LIFF app of its own because a LIFF app has one
endpoint. `?club=` opens that club's breakdown.
"""

_WEEKDAYS = "一二三四五六日"


def _when(game: GameRow, season: SeasonRow) -> str:
    """ "10/7（二）20:00", the way the app writes a game: the game's own
    start time, else the season's.
    """
    d = game.date
    text = f"{d.month}/{d.day}（{_WEEKDAYS[d.weekday()]}）"
    start = game.start_time or season.game_start_time
    return f"{text}{start:%H:%M}" if start is not None else text


def _message(title: str, club: str, body: list[str], link: tuple[str, str]) -> str:
    """Every notice has one shape: 【球隊】 and what the notice is, the facts
    one per line, then where to go, separated by blank lines.

    No line is longer than about ten Chinese characters, which is what
    fits a LINE bubble on a small phone without wrapping. Longer facts
    are split over two lines, the second in （）. The link always wraps,
    so its label sits on its own line.
    """
    label, url = link
    return "\n".join([f"【{club}】{title}", "", *body, "", f"{label}：", url])


ZERO = Decimal("0")


@dataclass(frozen=True)
class _Roster:
    """Who is expected at a game, counted the way the roster screen does."""

    playing: list[int]
    absent: int
    queued: int


def _roster(session: Session, game: GameRow, season: SeasonRow) -> _Roster:
    """Fixed members minus live absences, plus confirmed drop-ins."""
    absent_ids = {
        player_id
        for (player_id,) in session.query(AbsenceRow.player_id).filter(
            AbsenceRow.game_id == game.id, AbsenceRow.cancelled_at.is_(None)
        )
    }
    member_ids = [
        player_id
        for (player_id,) in session.query(SeasonMemberRow.player_id).filter(
            SeasonMemberRow.season_id == season.id
        )
    ]
    drop_in_ids = [
        player_id
        for (player_id,) in session.query(DropInRow.player_id).filter(
            DropInRow.game_id == game.id, DropInRow.playing()
        )
    ]
    queued = (
        session.query(func.count(DropInRow.id))
        .filter(DropInRow.game_id == game.id, DropInRow.queued())
        .scalar()
    )
    return _Roster(
        playing=[p for p in member_ids if p not in absent_ids] + drop_in_ids,
        absent=len([p for p in member_ids if p in absent_ids]),
        queued=int(queued or 0),
    )


def _organizer_line_ids(session: Session, club_id: int) -> list[str]:
    rows = (
        session.query(PlayerRow.line_user_id)
        .join(ClubMemberRow, ClubMemberRow.player_id == PlayerRow.id)
        .filter(
            ClubMemberRow.club_id == club_id,
            ClubMemberRow.role == "organizer",
            PlayerRow.line_user_id.is_not(None),
        )
        .all()
    )
    return [row[0] for row in rows]


def _push_each(line_user_ids: list[str], text: str, what: str) -> int:
    """Pushes to each person separately, so one refusal (somebody who has
    not added the Official Account) does not stop the rest.
    """
    told = 0
    for line_user_id in line_user_ids:
        try:
            push_to_user(line_user_id, text)
            told += 1
        except Exception:
            logger.exception("Couldn't send %s to one recipient", what)
    return told


def roster_status_text(session: Session, game: GameRow, season: SeasonRow) -> str:
    roster = _roster(session, game, season)
    playing = len(roster.playing)
    short = season.capacity - playing
    body = [
        f"場次：{_when(game, season)}",
        "",
        f"名單：{playing}／{season.capacity} 人",
        "（已滿）" if short <= 0 else f"（尚缺 {short} 人）",
    ]
    if playing < season.minimum_roster:
        body.append(f"（低於最低人數 {season.minimum_roster} 人）")
    body += ["", f"請假：{roster.absent} 人", f"候補：{roster.queued} 人"]
    return _message(
        "名單確定", _club_name(session, season.club_id), body, ("場次與報名", APP_URL)
    )


def send_roster_status(session: Session, game: GameRow) -> int:
    """Sends the roster status to the club's organizers only; members see
    the roster in the app. Returns how many organizers it reached.
    """
    season = session.get(SeasonRow, game.season_id)
    assert season is not None  # game.season_id is a foreign key, always valid
    recipients = _organizer_line_ids(session, season.club_id)
    if not recipients:
        logger.warning(
            "Game %s in club %s reached its deadline, but no organizer has a "
            "LINE account to tell",
            game.id,
            season.club_id,
        )
        return 0
    return _push_each(
        recipients, roster_status_text(session, game, season), "a roster status"
    )


def _taiwan_now() -> datetime:
    """Wall-clock time where the clubs play, the terms game dates and
    times are stored in.
    """
    return (
        datetime.now(UTC).astimezone(timezone(timedelta(hours=8))).replace(tzinfo=None)
    )


def send_deadline_notices(session: Session, now: datetime) -> int:
    """Sends everything due at a deadline that has passed, for every game
    today or later. Run every ten minutes; returns how many games it
    sent for.

    Each game is marked and committed before anything is sent: a LINE
    message cannot be taken back, so an overlapping or failed run must
    find the game already claimed rather than send twice.
    """
    due: list[GameRow] = []
    for game, season in (
        session.query(GameRow, SeasonRow)
        .join(SeasonRow, SeasonRow.id == GameRow.season_id)
        .filter(
            GameRow.status == GameStatus.SCHEDULED,
            GameRow.roster_notice_sent_at.is_(None),
            GameRow.date >= now.date(),
        )
        .with_for_update(of=GameRow, skip_locked=True)
        .all()
    ):
        deadline = change_deadline(
            game.date,
            game.start_time or season.game_start_time,
            season.change_deadline_hours,
        )
        if deadline <= now:
            game.roster_notice_sent_at = now
            due.append(game)
    session.commit()

    for game in due:
        send_roster_status(session, game)
    # Then everybody who has come off a queue since, including at the games
    # just claimed.
    send_promotion_notices(session, now)
    return len(due)


def send_promotion_notices(session: Session, now: datetime) -> int:
    """Tells everybody who came off the queue onto court, in a game whose
    deadline notices have gone out, and has not been told yet. Returns
    how many messages went out.

    Read at send time, so somebody promoted and bumped back between
    two runs is never told, and a row promoted twice is told once.
    Marked and committed before sending, like the roster status.
    """
    owed = (
        session.query(DropInRow)
        .join(GameRow, GameRow.id == DropInRow.game_id)
        .filter(
            GameRow.status == GameStatus.SCHEDULED,
            GameRow.roster_notice_sent_at.is_not(None),
            GameRow.date >= now.date(),
            DropInRow.playing(),
            DropInRow.queued_at.is_not(None),
            DropInRow.promotion_notice_sent_at.is_(None),
        )
        .order_by(GameRow.date, DropInRow.id)
        .with_for_update(of=DropInRow, skip_locked=True)
        .all()
    )
    promoted = [(drop_in.game_id, drop_in.player_id) for drop_in in owed]
    for drop_in in owed:
        drop_in.promotion_notice_sent_at = now
    session.commit()
    return tell_promoted(session, promoted)


_JOIN_NAMES_SHOWN = 8
"""How many waiting names the digest lists before counting the rest."""


def send_join_request_digests(session: Session) -> int:
    """One message per club with people waiting to be let in, never one
    per person. Returns how many clubs had somebody waiting.
    """
    names: dict[int, list[str]] = defaultdict(list)
    for club_id, name in (
        session.query(ClubMemberRow.club_id, PlayerRow.name)
        .join(PlayerRow, PlayerRow.id == ClubMemberRow.player_id)
        .filter(ClubMemberRow.status == "pending")
        .order_by(ClubMemberRow.joined_at)
    ):
        names[club_id].append(name)
    for club_id, waiting in names.items():
        club = session.get(ClubRow, club_id)
        club_name = club.name if club is not None else ""
        # One name a line; past a handful the rest are counted.
        shown = waiting[:_JOIN_NAMES_SHOWN]
        body = [f"待核准：{len(waiting)} 人", *shown]
        if len(waiting) > len(shown):
            body.append(f"（另 {len(waiting) - len(shown)} 人）")
        # A path appended to a LIFF link opens the wrong page, so the link
        # goes to the app and the line above says where to go from there.
        text = _message(
            "加入申請",
            club_name,
            [*body, "", "請至「管理 › 名單」", "核准或拒絕。"],
            ("VolleyFlow", APP_URL),
        )
        _push_each(_organizer_line_ids(session, club_id), text, "a join digest")
    return len(names)


def _line_ids_for(session: Session, player_ids: list[int]) -> dict[int, str]:
    """Player id -> LINE id, for whoever has one. A guest typed in by
    hand has no LINE account and simply cannot be told.
    """
    if not player_ids:
        return {}
    rows = (
        session.query(PlayerRow.id, PlayerRow.line_user_id)
        .filter(PlayerRow.id.in_(player_ids), PlayerRow.line_user_id.is_not(None))
        .all()
    )
    return {row[0]: row[1] for row in rows}


def _club_name(session: Session, club_id: int) -> str:
    club = session.get(ClubRow, club_id)
    return club.name if club is not None else ""


def tell_promoted(session: Session, promoted: list[tuple[int, int]]) -> int:
    """Sends the 遞補通知 to each person and whoever signed them up, naming
    which night is theirs. Returns how many messages went out.

    Takes (game_id, player_id) pairs because one run can cover several
    nights. Call after the commit: a push sent inside a transaction
    that rolls back cannot be taken back.
    """
    told = 0
    for game_id, player_id in promoted:
        game = session.get(GameRow, game_id)
        if game is None:
            continue
        season = session.get(SeasonRow, game.season_id)
        if season is None:
            continue
        drop_in = (
            session.query(DropInRow)
            .filter(
                DropInRow.game_id == game_id,
                DropInRow.player_id == player_id,
                DropInRow.playing(),
            )
            .first()
        )
        bringer_id = drop_in.brought_by_player_id if drop_in is not None else None
        player = session.get(PlayerRow, player_id)
        if player is None:
            continue
        bringer = session.get(PlayerRow, bringer_id) if bringer_id else None
        # The same words for the person and whoever signed them up.
        body = [f"場次：{_when(game, season)}", "", f"遞補上場：{player.name}"]
        text = _message(
            "遞補通知",
            _club_name(session, season.club_id),
            body,
            ("場次與報名", APP_URL),
        )
        recipients = [
            p.line_user_id
            for p in (player, bringer)
            if p is not None and p.line_user_id is not None
        ]
        told += _push_each(list(dict.fromkeys(recipients)), text, "a promotion notice")
    return told


def notify_game_cancelled(session: Session, game: GameRow) -> int:
    """Tells everybody expected at a game still to come that it is off:
    members not on leave, confirmed drop-ins, and whoever signed those
    up. Returns how many messages went out. Call after the commit.
    """
    if game.date < _taiwan_now().date():
        return 0
    season = session.get(SeasonRow, game.season_id)
    if season is None:
        return 0
    roster = _roster(session, game, season)
    bringers = [
        bringer_id
        for (bringer_id,) in session.query(DropInRow.brought_by_player_id).filter(
            DropInRow.game_id == game.id,
            DropInRow.playing(),
            DropInRow.brought_by_player_id.is_not(None),
        )
    ]
    reachable = _line_ids_for(session, list(dict.fromkeys(roster.playing + bringers)))
    refunded = game.status == GameStatus.CANCELLED_REFUNDED
    text = _message(
        "場次取消",
        _club_name(session, season.club_id),
        [
            f"場次：{_when(game, season)}",
            "",
            "本場已取消，",
            "本場費用已退還。" if refunded else "費用照常計收。",
        ],
        ("場次與報名", APP_URL),
    )
    return _push_each(list(dict.fromkeys(reachable.values())), text, "a cancellation")


@dataclass(frozen=True)
class FeeDue:
    """What one member is asked to pay for a season. All amounts are
    positive except `earlier`, which is signed the ledger's way:
    positive is a credit coming off.
    """

    player_id: int
    fee: Decimal
    earlier: Decimal
    due: Decimal
    previous_refund: Decimal = Decimal(0)
    """Last season's absence refund, when it is part of `earlier`."""
    previous_absences: int = 0


def notify_fee_due(session: Session, season: SeasonRow, dues: list[FeeDue]) -> set[int]:
    """Sends each member their 繳費通知 and returns who it reached. Anyone
    LINE refuses is left out, so they can be sent it later.
    """
    reachable = _line_ids_for(session, [d.player_id for d in dues])
    told: set[int] = set()
    for due in dues:
        line_user_id = reachable.get(due.player_id)
        if line_user_id is None:
            continue
        try:
            push_to_user(line_user_id, fee_notice_text(session, season, due))
            told.add(due.player_id)
        except Exception:
            logger.exception("Couldn't send player %s their fee notice", due.player_id)
    return told


def fee_notice_text(session: Session, season: SeasonRow, due: FeeDue) -> str:
    """The message itself, also shown on the confirmation sheet, so the
    organizer approves exactly what is sent.
    """
    games = (
        session.query(GameRow.date)
        .filter(
            GameRow.season_id == season.id,
            GameRow.status != GameStatus.CANCELLED_REFUNDED,
        )
        .order_by(GameRow.date)
        .all()
    )
    body = []
    if games:
        first, last = games[0][0], games[-1][0]
        body += [
            f"季別：{first.month}/{first.day}–{last.month}/{last.day}",
            f"共 {len(games)} 場",
            "",
        ]
    body.append(f"本季季費：${due.fee}")
    body.extend(_earlier_lines(due))
    body += ["", f"應繳金額：${due.due}"]
    return _message(
        "繳費通知",
        _club_name(session, season.club_id),
        body,
        ("帳務明細", f"{LEDGER_URL}?club={season.club_id}"),
    )


def _earlier_lines(due: FeeDue) -> list[str]:
    """What last season left: the refund for covered absences and
    anything else outstanding. Nothing when last season left nothing,
    since "上季餘額：$0" reads like an error.
    """
    if due.earlier == 0:
        return []
    lines = []
    rest = due.earlier
    if due.previous_refund > 0:
        lines += [
            f"上季退費：−${due.previous_refund}",
            f"（請假 {due.previous_absences} 次）",
        ]
        rest -= due.previous_refund
    if rest < 0:
        lines.append(f"上季未繳：+${-rest}")
    elif rest > 0:
        lines.append(f"上季餘額：−${rest}")
    return lines


if __name__ == "__main__":
    # Two jobs: `deadline` every ten minutes (deadline-notices.yml), the
    # join digest once a day (reminders.yml).
    logging.basicConfig(level=logging.INFO)
    job = sys.argv[1] if len(sys.argv) > 1 else "daily"
    with get_session() as db_session:
        if job == "deadline":
            games = send_deadline_notices(db_session, _taiwan_now())
            print(f"Sent deadline notices for {games} game(s)")
        else:
            waiting_clubs = send_join_request_digests(db_session)
            print(f"{waiting_clubs} club(s) have people waiting to join")
