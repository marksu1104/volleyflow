"""What the whole installation looks like, for the one person who keeps
it running.

Read-only and deliberately shallow: counts, not rows, and no money. The
job of this is to notice something odd — a club that never got a season,
people stuck waiting for an organizer who hasn't looked, reports piling
up unread — and then go and look at it properly in the app. Anything
that would show a particular person's balance belongs to their own
club's organizer.

Gated exactly like routes/reports.py, and written out again rather than
imported from it: nothing imports a route module (see routes/__init__).
"""

import os

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from volleyflow.api.dependencies import get_db
from volleyflow.api.routes._people import (
    _today_in_taiwan,
    get_current_player,
    is_developer,
)
from volleyflow.api.schemas import DeveloperOverviewOut
from volleyflow.db.models import (
    ClubMemberRow,
    ClubRow,
    DropInRow,
    GameRow,
    LedgerEntryRow,
    PlayerRow,
    ProblemReportRow,
    SeasonRow,
    WaitlistEntryRow,
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


@router.get("/developer/overview", response_model=DeveloperOverviewOut)
def developer_overview(
    db: Session = Depends(get_db),
    current_player: PlayerRow = Depends(get_current_player),
) -> DeveloperOverviewOut:
    """Every figure on one screen, each one a plain count."""
    _require_developer(current_player)
    today = _today_in_taiwan()

    newest = db.query(ClubRow.name).order_by(ClubRow.id.desc()).limit(5).all()

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
        drop_ins=db.query(DropInRow).filter(DropInRow.cancelled_at.is_(None)).count(),
        waitlist_entries=db.query(WaitlistEntryRow).count(),
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
    )
