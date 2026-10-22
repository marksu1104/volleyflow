"""The audit trail: one row per important change, written in the same
transaction as the change itself (see db.models.AuditEventRow).

"Important" means it moves money or people: payments and their undoing,
who is in a club or on a season's roster, settling, cancelling a game,
the venue cost and the air conditioning. The developer's overview lists
them newest first (routes/developer.py).
"""

from decimal import Decimal

from sqlalchemy import func
from sqlalchemy.orm import Session

from volleyflow.api.routes._people import _now
from volleyflow.db.models import AuditEventRow, ClubRow, GameRow, PlayerRow, SeasonRow

ACTIONS = frozenset(
    {
        "payment",
        "payment_undone",
        "club_created",
        "club_deleted",
        "member_approved",
        "member_removed",
        "players_merged",
        "season_started",
        "season_deleted",
        "season_settled",
        "season_unsettled",
        "fee_notice_sent",
        "venue_cost_changed",
        "ac_surcharge_changed",
        "capacity_changed",
        "ac_forecast_corrected",
        "ac_on",
        "ac_off",
        "game_cancelled_refunded",
        "game_cancelled",
        "game_updated",
        "roster_added",
        "roster_removed",
    }
)
"""Every action the trail records. developer.html turns each into a
sentence, and tests/frontend/developer.test.js reads this list to check
that none is missing one."""


def _audit(
    db: Session,
    actor: PlayerRow,
    action: str,
    club_id: int | None,
    *,
    subject_id: int | None = None,
    subject_name: str | None = None,
    amount: Decimal | int | None = None,
    detail: str | None = None,
) -> None:
    """Adds the row; the caller's commit writes it with the change.

    Names are looked up now and copied in, so the record still reads the
    same after a merge, a rename or the club being deleted.
    """
    if action not in ACTIONS:
        raise ValueError(f"unknown audit action {action!r}")
    club = db.get(ClubRow, club_id) if club_id is not None else None
    if subject_id is not None and subject_name is None:
        subject = db.get(PlayerRow, subject_id)
        subject_name = subject.name if subject is not None else None
    db.add(
        AuditEventRow(
            occurred_at=_now(),
            action=action,
            actor_id=actor.id,
            actor_name=actor.name,
            club_id=club_id,
            club_name=club.name if club is not None else None,
            subject_name=subject_name,
            amount=Decimal(amount) if amount is not None else None,
            detail=detail,
        )
    )


def _season_span(db: Session, season: SeasonRow) -> str | None:
    """Names a season in the trail by its first and last game:
    2026-09-15~2026-12-08."""
    first, last = (
        db.query(func.min(GameRow.date), func.max(GameRow.date))
        .filter(GameRow.season_id == season.id)
        .one()
    )
    if first is None:
        return None
    return f"{first.isoformat()}~{last.isoformat()}"
