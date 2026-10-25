"""Clubs: creating one, reading and renaming it, the clubs a player is in,
and deleting one."""

from decimal import Decimal

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    status,
)
from sqlalchemy import func
from sqlalchemy.orm import Session

from volleyflow.api.dependencies import get_db
from volleyflow.api.routes._attendance import (
    _delete_season_rows,
)
from volleyflow.api.routes._audit import _audit
from volleyflow.api.routes._money import _club_balances_up_to_now
from volleyflow.api.routes._people import (
    _get_club_or_404,
    _now,
    _require_club_access,
    _require_organizer,
    _today_in_taiwan,
    get_current_player,
    is_developer,
)
from volleyflow.api.schemas import (
    ClubCreate,
    ClubOut,
    ClubUpdate,
    MyClubOut,
)
from volleyflow.db.models import (
    ClubMemberRow,
    ClubRow,
    LedgerEntryRow,
    PlayerRow,
    SeasonRow,
)

router = APIRouter()


@router.post("/clubs", response_model=ClubOut)
def create_club(
    payload: ClubCreate,
    db: Session = Depends(get_db),
    current_player: PlayerRow = Depends(get_current_player),
) -> ClubOut:
    """Creates a club; the verified caller becomes its organizer."""
    club = ClubRow(name=payload.name, created_at=_now())
    db.add(club)
    db.flush()
    db.add(
        ClubMemberRow(
            club_id=club.id,
            player_id=current_player.id,
            role="organizer",
            joined_at=_now(),
        )
    )
    _audit(db, current_player, "club_created", club.id)
    db.commit()
    db.refresh(club)
    return ClubOut(id=club.id, name=club.name)


@router.patch("/clubs/{club_id}", response_model=ClubOut)
def rename_club(
    club_id: int,
    payload: ClubUpdate,
    db: Session = Depends(get_db),
    current_player: PlayerRow = Depends(get_current_player),
) -> ClubOut:
    club = _get_club_or_404(db, club_id)
    _require_organizer(db, club_id, current_player)
    club.name = payload.name
    db.commit()
    return ClubOut(id=club.id, name=club.name, role="organizer")


@router.get("/clubs", response_model=list[ClubOut])
def list_clubs(
    db: Session = Depends(get_db),
    current_player: PlayerRow = Depends(get_current_player),
) -> list[ClubOut]:
    """Only the caller's own clubs, so club ids are not handed out. A
    club one is not in yet is reached through its invite link.
    """
    rows = (
        db.query(ClubRow, ClubMemberRow.role)
        .join(ClubMemberRow, ClubMemberRow.club_id == ClubRow.id)
        .filter(ClubMemberRow.player_id == current_player.id)
        .order_by(ClubRow.id)
        .all()
    )
    if not is_developer(current_player):
        return [ClubOut(id=c.id, name=c.name, role=role) for c, role in rows]
    # The developer may run any club (see _runs_club). Every club they do
    # not organize themselves, including one they merely belong to, is
    # marked developer_view; `role` stays what they really are.
    role_in = {c.id: role for c, role in rows}
    listed = [
        ClubOut(
            id=c.id,
            name=c.name,
            role=role_in.get(c.id),
            developer_view=role_in.get(c.id) != "organizer",
        )
        for c in db.query(ClubRow).order_by(ClubRow.id)
    ]
    # Their own clubs first, so a page with nothing remembered opens on one.
    return sorted(listed, key=lambda club: club.developer_view)


@router.get("/clubs/{club_id}", response_model=ClubOut)
def get_club(
    club_id: int,
    db: Session = Depends(get_db),
    current_player: PlayerRow = Depends(get_current_player),
) -> ClubOut:
    """A club's name, for its members only. An invite prompt reads GET
    /invites/{token} instead, so club names cannot be found by trying
    ids.
    """
    club = _get_club_or_404(db, club_id)
    _require_club_access(db, club_id, current_player)
    return ClubOut(id=club.id, name=club.name)


@router.get("/players/{player_id}/clubs", response_model=list[MyClubOut])
def list_player_clubs(
    player_id: int,
    db: Session = Depends(get_db),
    current_player: PlayerRow = Depends(get_current_player),
) -> list[MyClubOut]:
    """Every club this player belongs to, with their role and balance in
    each, for the profile page. Self only.
    """
    if current_player.id != player_id:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "You can only list your own clubs"
        )
    rows = (
        db.query(ClubRow, ClubMemberRow)
        .join(ClubMemberRow, ClubMemberRow.club_id == ClubRow.id)
        .filter(ClubMemberRow.player_id == player_id)
        .order_by(ClubRow.id)
        .all()
    )
    organized = [club.id for club, m in rows if m.role == "organizer"]
    waiting = {
        club_id: count
        for club_id, count in db.query(ClubMemberRow.club_id, func.count())
        .filter(ClubMemberRow.club_id.in_(organized), ClubMemberRow.status == "pending")
        .group_by(ClubMemberRow.club_id)
        .all()
    }
    # One grouped query for every club's balance, up to each club's season
    # in play. Keyed on player_id alone: linking and merging never leave
    # money under an id the player no longer has.
    owed = _club_balances_up_to_now(db, player_id, _today_in_taiwan())
    return [
        MyClubOut(
            id=club.id,
            name=club.name,
            role=membership.role,
            wants_fixed_membership=membership.wants_fixed_membership,
            status=membership.status,
            pending_count=waiting.get(club.id, 0),
            balance=owed.get(club.id, Decimal(0)),
        )
        for club, membership in rows
    ]


@router.delete("/clubs/{club_id}", status_code=204)
def delete_club(
    club_id: int,
    db: Session = Depends(get_db),
    current_player: PlayerRow = Depends(get_current_player),
) -> None:
    """Deletes a club and every season in it. Refused if any season is
    settled, as for deleting a season. Players are global and are
    never deleted; only their membership of this club goes.
    """
    _get_club_or_404(db, club_id)
    _require_organizer(db, club_id, current_player)

    seasons = db.query(SeasonRow).filter(SeasonRow.club_id == club_id).all()
    if any(s.settled_at is not None for s in seasons):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "A season in this club is settled — its books can't be deleted",
        )

    for season in seasons:
        _delete_season_rows(db, season)
    db.query(LedgerEntryRow).filter(LedgerEntryRow.club_id == club_id).delete(
        synchronize_session=False
    )
    db.query(ClubMemberRow).filter(ClubMemberRow.club_id == club_id).delete(
        synchronize_session=False
    )
    _audit(db, current_player, "club_deleted", club_id)
    club = db.get(ClubRow, club_id)
    if club is not None:
        db.delete(club)
    db.commit()
