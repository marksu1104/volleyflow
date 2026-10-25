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
    """Whoever creates a club becomes its organizer.
    The creator is the verified caller, not a client-supplied id.
    """
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
    """Only the caller's own clubs. This used to return every club that
    existed, to anyone — which both leaked the club list and handed out
    the ids that made the other reads enumerable. Joining a club you're
    not in yet goes through its invite link, which carries the id; see
    GET /clubs/{id} for the name lookup that link needs.
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
    # The developer may run any club (see _runs_club), so the management
    # pages offer them all. Every club they don't organize themselves is
    # marked developer_view — one they merely belong to included. Marking
    # only the clubs they had no membership in at all left a club they
    # had joined as a member listed as role "member", which the
    # management pages filter out, so its 管理 opened their own club
    # instead (reported 2026-10-12). `role` stays what they really are.
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
    """A club's name — for its members.

    It used to be readable by anyone signed in, for an invite prompt that
    needed a name before the visitor had joined. That prompt reads
    GET /invites/{token} now, and nothing in the app calls this for a club
    it isn't in — so left open, all it still did was let anybody with a
    LINE account read every club's name by trying ids in order, which is
    exactly what the invite token was introduced to stop. Found on
    2026-09-15 by tests/api/test_tenant_isolation.py.
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
    """Every club this player belongs to and their role in each — what a
    profile page's club list needs, spanning clubs the way a single
    club's member list (GET /clubs/{id}/members) can't. Self only: which
    clubs someone else belongs to isn't this app's business to hand to a
    third party, including another club's organizer.
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
    # One grouped query for every club's balance, the same shape as the
    # pending count above and as list_club_balances: a number per club,
    # never a ledger per club. Reading the full entry history once per
    # club to add up a single figure in the browser is the N+1 behind
    # 「個人資料頁面的我的球隊，每次都會進入網頁後過很久才載入出現」.
    #
    # Safe to key on player_id alone: linking refuses when the LINE row
    # already has ledger entries, and merging re-points them, so a
    # player's money never sits under an id they no longer are.
    #
    # Up to each club's season in play, not the whole ledger: a season
    # booked ahead has charged its fee already, and counting it here put
    # a figure on 我的帳務 that no 繳費通知 had asked for.
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
    """Deletes a club and every season in it. Same reasoning and the same
    refusal as deleting a season: a settled season anywhere in the club
    blocks it, because that's real closed books. Players themselves are
    never deleted — a Player is global and outlives any one club;
    only their membership of this club goes.
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
