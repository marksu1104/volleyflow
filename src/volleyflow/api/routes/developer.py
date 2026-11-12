"""An overview of the whole installation, for the developer.

Read-only counts, no balances: enough to notice something odd (a club
that never started a season, people waiting for approval, unread
reports, a crash) and go look. The exceptions are the last week's
server errors and the audit trail, which shows each payment's amount
but never a balance.

Gated like routes/reports.py, repeated rather than imported because
nothing imports a route module.
"""

import os
from collections import defaultdict
from datetime import UTC, date, datetime, time, timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from volleyflow.api.dependencies import get_db
from volleyflow.api.routes._audit import ATTENDANCE_ACTIONS
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
    DeveloperNightOut,
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
    # Fails closed: unset means nobody can read this.
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
    use. Each opens that club's management pages, where the developer
    may act as organizer.
    """
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
    """Every figure on one screen, each a plain count."""
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
        # Old screenshot-only rows have nothing to read.
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
    club_id: int | None = None,
    night: date | None = None,
    db: Session = Depends(get_db),
    current_player: PlayerRow = Depends(get_current_player),
) -> list[AuditEventOut]:
    """The audit trail, newest first; see db.models.AuditEventRow.

    With `club_id` and `night`, everything on the record against that
    club's night instead, oldest first, so it reads as what happened:
    attendance, and the payments and air conditioning that name the
    night too.
    """
    _require_developer(current_player)
    if (club_id is None) != (night is None):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "A night is read with its club"
        )
    query = db.query(AuditEventRow)
    if club_id is not None and night is not None:
        rows = (
            query.filter(
                AuditEventRow.club_id == club_id,
                AuditEventRow.detail == night.isoformat(),
            )
            .order_by(AuditEventRow.occurred_at, AuditEventRow.id)
            .limit(500)
            .all()
        )
    else:
        rows = (
            query.order_by(AuditEventRow.occurred_at.desc(), AuditEventRow.id.desc())
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


@router.get("/developer/activity/nights", response_model=list[DeveloperNightOut])
def developer_activity_nights(
    db: Session = Depends(get_db),
    current_player: PlayerRow = Depends(get_current_player),
) -> list[DeveloperNightOut]:
    """The 30 most recent game nights with attendance on the record,
    latest first, each with its club: the choices for reading the
    trail one night at a time."""
    _require_developer(current_player)
    rows = (
        db.query(
            AuditEventRow.club_id,
            func.max(AuditEventRow.club_name),
            AuditEventRow.detail,
        )
        .filter(
            AuditEventRow.action.in_(ATTENDANCE_ACTIONS),
            AuditEventRow.club_id.is_not(None),
            AuditEventRow.detail.is_not(None),
        )
        .group_by(AuditEventRow.club_id, AuditEventRow.detail)
        .order_by(AuditEventRow.detail.desc(), AuditEventRow.club_id)
        .limit(30)
        .all()
    )
    return [
        DeveloperNightOut(club_id=club_id, club_name=name, night=date.fromisoformat(d))
        for club_id, name, d in rows
    ]
