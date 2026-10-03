"""Short-roster alerts, to the organizers of the club the game belongs to.

Per the attendance rules: "If the roster is short, only the organizer is
notified — never the waitlist."
"""

import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
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
from volleyflow.schedule import GameStatus

logger = logging.getLogger("volleyflow.reminders")

# Every push ends with this. A notice that names a problem without
# offering a way to act on it makes the reader go and find the app
# themselves, and the join-request digest was worse than that — it said
# "go to 管理成員" and gave no route at all.
#
# The liff.line.me address rather than the GitHub Pages one: only a LIFF
# URL opens inside LINE carrying an identity. The plain site address,
# tapped from a chat, lands on liff.login() and bounces the reader to a
# LINE login screen for no reason.
#
# This is the member app — the same destination as the rich menu's first
# button, so the app has one front door rather than several. There is no
# LIFF app pointing at the organizer pages, so a digest cannot deep-link
# to 管理成員; it names the screen and lets the reader get there.
#
# Adding it costs nothing: it rides inside a message already being sent,
# and the free tier counts messages, not characters.
APP_URL = "https://liff.line.me/2011156233-6CouG6VI"
LEDGER_URL = "https://liff.line.me/2011156233-SWicoUre"
"""我的帳務 (frontend/ledger.html) — its own LIFF app, since one LIFF app
has one endpoint. `?club=` opens that club's breakdown directly."""

_WEEKDAYS = "一二三四五六日"


def _when(game: GameRow, season: SeasonRow) -> str:
    """ "10/7（二）20:00" — the way the app itself writes a game. The time
    is the game's own when it has one, else the season's usual one."""
    d = game.date
    text = f"{d.month}/{d.day}（{_WEEKDAYS[d.weekday()]}）"
    start = game.start_time or season.game_start_time
    return f"{text} {start:%H:%M}" if start is not None else text


def _message(title: str, club: str, body: list[str], link: tuple[str, str]) -> str:
    """Every notice has one shape (agreed 2026-10-03): 【球隊】 and what
    the notice is, the facts one per line, then where to go. Blank lines
    between the three, because LINE shows them as one block otherwise."""
    label, url = link
    return "\n".join([f"【{club}】{title}", "", *body, "", f"{label}：", url])


ZERO = Decimal("0")


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


def _organizer_line_ids(session: Session, club_id: int) -> list[str]:
    """Everyone who organizes this club and can be reached over LINE.

    This used to be one environment variable, `LINE_ORGANIZER_USER_ID`,
    which was right when the app ran one club and wrong from the day it
    became multi-tenant (2026-09-06): every club's short game alerted the
    developer, and no other club's organizer was ever told. Found on
    2026-09-15 while getting ready to hand the app to other people, when
    "the organizer" stopped meaning one person.
    """
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


def send_game_reminder(session: Session, game: GameRow) -> None:
    """The short-roster alert, to that club's organizers alone.

    There is deliberately no message to the group chat. One was built —
    the roster and the price, the night before — and the organizer asked
    for it to be dropped: the group already talks about the game in the
    group, and a bot repeating the roster into that conversation is noise
    rather than news. The only thing worth interrupting anyone for is the
    thing nobody would otherwise notice in time, which is a game that
    doesn't have enough people yet.

    Each organizer is tried on their own. LINE only delivers a push to
    somebody who has added the Official Account as a friend, and refuses
    it with an error otherwise; letting that error escape stopped the
    whole nightly run at the first organizer who hadn't, so every club
    after them in the list went unalerted too.
    """
    season = session.get(SeasonRow, game.season_id)
    assert season is not None  # game.season_id is a foreign key, always valid

    roster = _expected_roster(session, game, season)
    if len(roster) >= season.minimum_roster:
        return

    club = session.get(ClubRow, season.club_id)
    club_name = club.name if club is not None else ""
    recipients = _organizer_line_ids(session, season.club_id)
    if not recipients:
        logger.warning(
            "Game %s in club %s is short-handed, but no organizer has a LINE "
            "account to tell",
            game.id,
            season.club_id,
        )
        return

    # The club's name leads, because one person can organize more than
    # one club and a date alone doesn't say which.
    text = _message(
        "人數不足",
        club_name,
        [
            f"場次：{_when(game, season)}",
            f"目前人數：{len(roster)} 人",
            f"最低人數：{season.minimum_roster} 人",
        ],
        ("場次與報名", APP_URL),
    )
    for line_user_id in recipients:
        try:
            push_to_user(line_user_id, text)
        except Exception:
            logger.exception(
                "Couldn't alert an organizer of club %s — most often they "
                "haven't added the Official Account as a friend",
                season.club_id,
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


def send_join_request_digests(session: Session) -> int:
    """One message per club with people waiting to be let in, from the
    daily run — never one per person, which would spend a month's pushes
    in the first week. Returns how many clubs had somebody waiting."""
    waiting = (
        session.query(ClubMemberRow.club_id, func.count())
        .filter(ClubMemberRow.status == "pending")
        .group_by(ClubMemberRow.club_id)
        .all()
    )
    for club_id, count in waiting:
        club = session.get(ClubRow, club_id)
        club_name = club.name if club is not None else ""
        # Not a link to the 名單 page itself: the app link opens the
        # member page, and a path appended to a LIFF link lands on the
        # wrong file. So the link says only where it goes, and the line
        # above it says where to go from there.
        text = _message(
            "新成員待核准",
            club_name,
            [f"待核准：{count} 人", "請至「管理 › 名單」核准或拒絕。"],
            ("VolleyFlow", APP_URL),
        )
        for line_user_id in _organizer_line_ids(session, club_id):
            try:
                push_to_user(line_user_id, text)
            except Exception:
                logger.exception(
                    "Couldn't tell an organizer of club %s who is waiting", club_id
                )
    return len(waiting)


def _line_ids_for(session: Session, player_ids: list[int]) -> dict[int, str]:
    """Player id -> LINE id, for whoever has one.

    A missing id is the ordinary case, not a failure: a guest the
    organizer typed in by hand has no LINE account at all, and neither
    does somebody's +1. They simply can't be told.
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


def notify_promoted_from_waitlist(
    session: Session, promoted: list[tuple[int, int]]
) -> int:
    """Tell whoever the queue just put on court. Returns how many were told.

    The one notification that changes what somebody does. Everything else
    here reports something the reader could have looked up; this one
    reaches a person who queued, went away, and has no reason to check
    again — while the slot is already counted as theirs and the roster
    says they are playing. Not telling them is how a game ends up a
    player short with nobody at fault.

    Takes `(game_id, player_id)` pairs rather than one game and a list of
    people. Removing somebody from a season's roster frees their place at
    *every* game they were expected at, so a single action can promote
    several people into several different nights, and each of them has to
    be told which night is theirs.

    **Call this after the commit, never before.** The promotion is one
    write in a transaction that can still roll back; a push sent from
    inside it would tell somebody they are playing in a game they were
    never actually promoted to, and LINE has no way to take it back.

    One try per person: LINE refuses a push to anybody who hasn't added
    the Official Account, and letting that error escape would stop
    everyone after them in the list — the failure send_game_reminder was
    fixed for on 2026-09-15.
    """
    if not promoted:
        return 0

    by_game: dict[int, list[int]] = defaultdict(list)
    for game_id, player_id in promoted:
        by_game[game_id].append(player_id)
    reachable = _line_ids_for(session, [player_id for _, player_id in promoted])

    told = 0
    for game_id, player_ids in by_game.items():
        game = session.get(GameRow, game_id)
        if game is None:
            continue
        season = session.get(SeasonRow, game.season_id)
        if season is None:
            continue
        # The club's name leads, for the same reason the short-roster
        # alert carries it: one person can play in more than one club and
        # a date alone doesn't say which.
        text = _message(
            "候補遞補通知",
            _club_name(session, season.club_id),
            [
                f"場次：{_when(game, season)}",
                "狀態：已由候補轉為上場",
                "",
                "如無法出席，請於 App 取消報名。",
            ],
            ("場次與報名", APP_URL),
        )
        for player_id in player_ids:
            line_user_id = reachable.get(player_id)
            if line_user_id is None:
                continue
            try:
                push_to_user(line_user_id, text)
                told += 1
            except Exception:
                logger.exception(
                    "Couldn't tell player %s they were promoted into game %s",
                    player_id,
                    game_id,
                )
    return told


@dataclass(frozen=True)
class FeeDue:
    """What one member is asked to pay for a season. Amounts are what the
    message states, all positive except `earlier`, which is signed the
    ledger's way: positive is a credit coming off."""

    player_id: int
    fee: Decimal
    earlier: Decimal
    due: Decimal
    previous_refund: Decimal = Decimal(0)
    """Last season's absence refund, when it is part of `earlier`."""
    previous_absences: int = 0


def notify_fee_due(session: Session, season: SeasonRow, dues: list[FeeDue]) -> set[int]:
    """Send each member their 繳費通知. Returns who it reached.

    The only message about money, and sent once a season: a kept refund
    or an unpaid balance from last season is folded in here rather than
    announced at settlement. Anyone LINE refuses — no account, or the
    Official Account never added — is left out of the result, so the
    caller can leave them free to be sent it later.
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
    """The message itself — also what the confirmation sheet shows, so
    what the organizer approves is word for word what is sent."""
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
        span = f"{first.month}/{first.day}–{last.month}/{last.day}"
        body.append(f"季別：{span}，共 {len(games)} 場")
    body.append(f"本季季費：${due.fee}")
    body.extend(_earlier_lines(due))
    body.append(f"應繳金額：${due.due}")
    return _message(
        "季費繳費通知",
        _club_name(session, season.club_id),
        body,
        ("帳務明細", f"{LEDGER_URL}?club={season.club_id}"),
    )


def _earlier_lines(due: FeeDue) -> list[str]:
    """What last season left, in the words a member can check: the
    refund for their absences, and whatever else is outstanding either
    way. Nothing at all when last season left nothing — "上季餘額：$0"
    reads like something went wrong. A refund handed over in cash at
    settlement is already squared, so `earlier` is 0 and it isn't
    mentioned either."""
    if due.earlier == 0:
        return []
    lines = []
    rest = due.earlier
    if due.previous_refund > 0:
        lines.append(
            f"上季請假退費：−${due.previous_refund}（{due.previous_absences} 次）"
        )
        rest -= due.previous_refund
    if rest < 0:
        lines.append(f"上季未繳：+${-rest}")
    elif rest > 0:
        lines.append(f"上季餘額：−${rest}")
    return lines


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    with get_session() as db_session:
        sent_count = send_reminders_for_date(
            db_session, date.today() + timedelta(days=1)
        )
        waiting_clubs = send_join_request_digests(db_session)
    print(
        f"Sent reminders for {sent_count} game(s); "
        f"{waiting_clubs} club(s) have people waiting to join"
    )
