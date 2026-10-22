"""What the whole installation looks like, for the one person who keeps
it running.

Read-only and deliberately shallow: counts, not rows, and no balances.
The job of this is to notice something odd — a club that never got a
season, people stuck waiting for an organizer who hasn't looked, reports
piling up unread, a crash — and then go and look at it properly in the
app. Anything that would show a particular person's balance belongs to
their own club's organizer.

Two lists are the exception, added 2026-10-22: the server errors of the
last week, and the audit trail. A trail of who changed what has to say
what the change was — a payment entered as 2,875 instead of 285 is
exactly the thing to catch — so it carries the amount of each payment,
never a running balance.

Gated exactly like routes/reports.py, and written out again rather than
imported from it: nothing imports a route module (see routes/__init__).
"""

import os
from collections import defaultdict
from datetime import UTC, datetime, time, timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from volleyflow.api.dependencies import get_db
from volleyflow.api.routes._people import (
    _TAIWAN,
    _today_in_taiwan,
    get_current_player,
    is_developer,
)
from volleyflow.api.schemas import (
    AuditEventOut,
    DeveloperClubOut,
    DeveloperErrorOut,
    DeveloperOverviewOut,
)
from volleyflow.db.models import (
    AuditEventRow,
    ClubMemberRow,
    ClubRow,
    DropInRow,
    ErrorEventRow,
    GameRow,
    LedgerEntryRow,
    PlayerRow,
    ProblemReportRow,
    SeasonRow,
)
from volleyflow.schedule import GameStatus

router = APIRouter()


def _require_developer(player: PlayerRow) -> None:
    # Fails closed, like the invite secret and the report reader: unset
    # means nobody can read this, never that everybody can.
    if not os.environ.get("DEVELOPER_LINE_USER_ID"):
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "The developer overview isn't configured on this server",
        )
    if not is_developer(player):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "Only the developer can read this"
        )


@router.get("/developer/clubs", response_model=list[DeveloperClubOut])
def developer_clubs(
    db: Session = Depends(get_db),
    current_player: PlayerRow = Depends(get_current_player),
) -> list[DeveloperClubOut]:
    """Every club, with enough to tell them apart and see which are in
    use. Each row opens that club's management pages, where the developer
    may act as its organizer."""
    _require_developer(current_player)
    organizers: dict[int, list[str]] = defaultdict(list)
    for club_id, name in (
        db.query(ClubMemberRow.club_id, PlayerRow.name)
        .join(PlayerRow, PlayerRow.id == ClubMemberRow.player_id)
        .filter(ClubMemberRow.role == "organizer")
        .order_by(PlayerRow.name)
    ):
        organizers[club_id].append(name)
    members: dict[int, int] = {
        club_id: count
        for club_id, count in db.query(ClubMemberRow.club_id, func.count()).group_by(
            ClubMemberRow.club_id
        )
    }
    seasons: dict[int, int] = {
        club_id: count
        for club_id, count in db.query(SeasonRow.club_id, func.count()).group_by(
            SeasonRow.club_id
        )
    }
    activity: dict[int, datetime] = {
        club_id: latest
        for club_id, latest in db.query(
            LedgerEntryRow.club_id, func.max(LedgerEntryRow.recorded_at)
        ).group_by(LedgerEntryRow.club_id)
    }
    return [
        DeveloperClubOut(
            id=club.id,
            name=club.name,
            organizers=organizers.get(club.id, []),
            members=int(members.get(club.id, 0)),
            seasons=int(seasons.get(club.id, 0)),
            last_activity=activity.get(club.id),
        )
        for club in db.query(ClubRow).order_by(ClubRow.id)
    ]


@router.get("/developer/overview", response_model=DeveloperOverviewOut)
def developer_overview(
    db: Session = Depends(get_db),
    current_player: PlayerRow = Depends(get_current_player),
) -> DeveloperOverviewOut:
    """Every figure on one screen, each one a plain count."""
    _require_developer(current_player)
    today = _today_in_taiwan()

    newest = db.query(ClubRow.name).order_by(ClubRow.id.desc()).limit(5).all()

    monday = today - timedelta(days=today.weekday())
    week_started = (
        datetime.combine(monday, time(), tzinfo=_TAIWAN)
        .astimezone(UTC)
        .replace(tzinfo=None)
    )
    active_clubs = (
        db.query(func.count(func.distinct(SeasonRow.club_id)))
        .join(GameRow, GameRow.season_id == SeasonRow.id)
        .filter(
            GameRow.status == GameStatus.SCHEDULED,
            GameRow.date >= today - timedelta(days=30),
            GameRow.date <= today + timedelta(days=30),
        )
        .scalar()
    )

    return DeveloperOverviewOut(
        clubs=db.query(ClubRow).count(),
        players=db.query(PlayerRow).count(),
        seasons=db.query(SeasonRow).count(),
        settled_seasons=db.query(SeasonRow)
        .filter(SeasonRow.settled_at.is_not(None))
        .count(),
        games=db.query(GameRow).count(),
        upcoming_games=db.query(GameRow)
        .filter(GameRow.date >= today, GameRow.status == GameStatus.SCHEDULED)
        .count(),
        club_members=db.query(ClubMemberRow).count(),
        pending_members=db.query(ClubMemberRow)
        .filter(ClubMemberRow.status == "pending")
        .count(),
        # Cancelled signups are history, not people expecting to play.
        drop_ins=db.query(DropInRow).filter(DropInRow.playing()).count(),
        waitlist_entries=db.query(DropInRow).filter(DropInRow.queued()).count(),
        ledger_entries=db.query(LedgerEntryRow).count(),
        # Rows written before 2026-09-15 are screenshots with no words —
        # there is nothing to read, so they are not reports here either.
        reports=db.query(ProblemReportRow)
        .filter(ProblemReportRow.message.is_not(None))
        .count(),
        unread_reports=db.query(ProblemReportRow)
        .filter(
            ProblemReportRow.message.is_not(None),
            ProblemReportRow.read_at.is_(None),
        )
        .count(),
        newest_clubs=[name for (name,) in newest],
        active_clubs=int(active_clubs or 0),
        games_this_week=db.query(GameRow)
        .filter(
            GameRow.status == GameStatus.SCHEDULED,
            GameRow.date >= monday,
            GameRow.date <= monday + timedelta(days=6),
        )
        .count(),
        signups_this_week=db.query(DropInRow)
        .filter(DropInRow.signed_up_at >= week_started)
        .count(),
    )


@router.get("/developer/errors", response_model=list[DeveloperErrorOut])
def developer_errors(
    db: Session = Depends(get_db),
    current_player: PlayerRow = Depends(get_current_player),
) -> list[DeveloperErrorOut]:
    """The server's failures of the last seven days, newest first."""
    _require_developer(current_player)
    since = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=7)
    rows = (
        db.query(ErrorEventRow)
        .filter(ErrorEventRow.occurred_at >= since)
        .order_by(ErrorEventRow.occurred_at.desc())
        .limit(100)
        .all()
    )
    return [
        DeveloperErrorOut(
            occurred_at=row.occurred_at,
            method=row.method,
            path=row.path,
            page=row.page,
            error_type=row.error_type,
            message=row.message,
            location=row.location,
        )
        for row in rows
    ]


@router.get("/developer/activity", response_model=list[AuditEventOut])
def developer_activity(
    limit: int = 50,
    db: Session = Depends(get_db),
    current_player: PlayerRow = Depends(get_current_player),
) -> list[AuditEventOut]:
    """The audit trail, newest first — see db.models.AuditEventRow."""
    _require_developer(current_player)
    rows = (
        db.query(AuditEventRow)
        .order_by(AuditEventRow.occurred_at.desc(), AuditEventRow.id.desc())
        .limit(max(1, min(limit, 200)))
        .all()
    )
    return [
        AuditEventOut(
            occurred_at=row.occurred_at,
            action=row.action,
            actor_name=row.actor_name,
            club_name=row.club_name,
            subject_name=row.subject_name,
            amount=row.amount,
            detail=row.detail,
        )
        for row in rows
    ]
