"""Every LINE message the app sends, and when.

At a game's change deadline (decided 2026-10-05), once: the roster
status to that club's organizers, and a promotion notice to everybody
who came off the waiting list, and to whoever signed them up. Held until
then because a roster keeps moving until the deadline — somebody takes
leave, then names a 代打 — and a notice sent at the first move could be
wrong by the second. After the deadline only the organizer can change
anything, and a promotion they make is told straight away.

Also: a game called off, to everybody expected at it; people waiting to
join, to the organizer, once a day; and the season's 繳費通知, when the
organizer sends it.
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
    return f"{text}{start:%H:%M}" if start is not None else text


def _message(title: str, club: str, body: list[str], link: tuple[str, str]) -> str:
    """Every notice has one shape (agreed 2026-10-03): 【球隊】 and what
    the notice is, the facts one per line, then where to go. Blank lines
    between the three, because LINE shows them as one block otherwise.

    No line longer than about ten Chinese characters (2026-10-05): a LINE
    bubble on a small phone is about 65% of a 320-wide screen, and a line
    that wraps there is the one people misread. Measured, not guessed —
    on 季費繳費通知 the organizer's own phone wrapped two lines. Facts
    that would run longer are split across two lines, the second in
    （）. The link itself cannot be shortened and always wraps, which is
    why its label sits on a line of its own."""
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
    """Fixed members minus this game's live absences, plus confirmed
    drop-ins. A cancelled absence is not an absence — it used to be
    counted as one here, which under-reported every roster where
    somebody had taken leave and then taken it back."""
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
    """One try per person. LINE refuses a push to anybody who hasn't
    added the Official Account, and letting that error escape stopped
    everyone after them in the list (fixed 2026-09-15)."""
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
    """The roster status, to that club's organizers alone — never the
    members, at the organizer's choice (2026-10-05): the app already shows
    the roster to anyone who looks, and one message per game is what the
    free tier can afford. Returns how many organizers it reached."""
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
    """Wall-clock time where the clubs play — the terms a game's date and
    start time are stored in."""
    return (
        datetime.now(UTC).astimezone(timezone(timedelta(hours=8))).replace(tzinfo=None)
    )


def send_deadline_notices(session: Session, now: datetime) -> int:
    """Everything due at a deadline that has passed, for every game still
    to be played today or later. Run every ten minutes; returns how many
    games it sent for.

    Each game is marked and committed *before* anything is sent. A LINE
    message cannot be taken back, so a run that overlaps another, or
    fails half way, must find the game already claimed rather than send
    it twice; the cost is that a failure mid-send leaves somebody untold,
    which the organizer can see on the roster anyway.
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
        promoted = [
            (game.id, player_id)
            for (player_id,) in session.query(DropInRow.player_id).filter(
                DropInRow.game_id == game.id,
                DropInRow.playing(),
                DropInRow.queued_at.is_not(None),
            )
        ]
        notify_promoted_from_waitlist(session, promoted)
    return len(due)


_JOIN_NAMES_SHOWN = 8
"""How many waiting names the digest lists before counting the rest."""


def send_join_request_digests(session: Session) -> int:
    """One message per club with people waiting to be let in, from the
    daily run — never one per person, which would spend a month's pushes
    in the first week. Returns how many clubs had somebody waiting."""
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
        # Who, not just how many (2026-10-05) — one name a line, so a long
        # list stays readable. Past a handful the rest are counted rather
        # than listed; the 名單 page has them all.
        shown = waiting[:_JOIN_NAMES_SHOWN]
        body = [f"待核准：{len(waiting)} 人", *shown]
        if len(waiting) > len(shown):
            body.append(f"（另 {len(waiting) - len(shown)} 人）")
        # Not a link to the 名單 page itself: the app link opens the
        # member page, and a path appended to a LIFF link lands on the
        # wrong file. So the link says only where it goes, and the line
        # above it says where to go from there.
        text = _message(
            "加入申請",
            club_name,
            [*body, "", "請至「管理 › 名單」", "核准或拒絕。"],
            ("VolleyFlow", APP_URL),
        )
        _push_each(_organizer_line_ids(session, club_id), text, "a join digest")
    return len(names)


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
    """Tell each person who came off the waiting list, and whoever signed
    them up, which night is theirs. Returns how many messages went out.

    Only once that game's deadline notices have gone out
    (`roster_notice_sent_at`). Before then this sends nothing: the batch
    at the deadline tells everyone who is promoted by then, which spares
    somebody a "you're in" that a 代打 named an hour later would undo.
    After it, only the organizer can still change the game, and whoever
    they promote is told straight away.

    Takes `(game_id, player_id)` pairs: removing somebody from a season's
    roster frees their place at every game, so one action can promote
    several people into several nights.

    **Call this after the commit, never before** — a push sent from
    inside a transaction that then rolls back cannot be taken back.
    """
    told = 0
    for game_id, player_id in promoted:
        game = session.get(GameRow, game_id)
        if game is None or game.roster_notice_sent_at is None:
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
        # The same words for the person and whoever signed them up; who
        # signed them up is not the news (2026-10-05).
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
    """Tell everybody expected at a game that it is off: the members not
    on leave, the confirmed drop-ins, and whoever signed those up. Before
    this, calling a game off reached nobody. Only for a game still to
    come; returns how many messages went out. Call after the commit."""
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
    # Two jobs, two schedules: `deadline` every ten minutes
    # (deadline-notices.yml), the join digest once a day (reminders.yml).
    logging.basicConfig(level=logging.INFO)
    job = sys.argv[1] if len(sys.argv) > 1 else "daily"
    with get_session() as db_session:
        if job == "deadline":
            games = send_deadline_notices(db_session, _taiwan_now())
            print(f"Sent deadline notices for {games} game(s)")
        else:
            waiting_clubs = send_join_request_digests(db_session)
            print(f"{waiting_clubs} club(s) have people waiting to join")
