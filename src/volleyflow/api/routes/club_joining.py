"""Getting into a club: its invite link, asking to join, and being let in."""

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    status,
)
from sqlalchemy.orm import Session

from volleyflow.api.dependencies import get_db
from volleyflow.api.invites import club_id_from_invite_token, invite_token
from volleyflow.api.routes._audit import _audit
from volleyflow.api.routes._people import (
    _gender,
    _get_club_or_404,
    _get_player_or_404,
    _now,
    _require_organizer,
    get_current_player,
)
from volleyflow.api.schemas import (
    ClubJoin,
    ClubMemberOut,
    InviteOut,
    JoinApproval,
    MemberOut,
    MembershipIntent,
    MyClubOut,
)
from volleyflow.db.models import (
    ClubMemberRow,
    ClubRow,
    PlayerRow,
)

router = APIRouter()


@router.get("/clubs/{club_id}/invite", response_model=InviteOut)
def get_club_invite(
    club_id: int,
    db: Session = Depends(get_db),
    current_player: PlayerRow = Depends(get_current_player),
) -> InviteOut:
    """The token for this club's join link. Organizer only. See
    api/invites.py.
    """
    club = _get_club_or_404(db, club_id)
    _require_organizer(db, club_id, current_player)
    return InviteOut(club_id=club.id, club_name=club.name, token=invite_token(club.id))


@router.get("/invites/{token}", response_model=InviteOut)
def resolve_invite(token: str, db: Session = Depends(get_db)) -> InviteOut:
    """The club a join link points at, for the "加入「晴光館」？" prompt. Needs
    no sign-in: the token is the gate, and forging one means guessing
    a 64-bit HMAC (see api/invites.py).
    """
    club_id = club_id_from_invite_token(token)
    if club_id is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Invite link not recognised")
    club = db.get(ClubRow, club_id)
    if club is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "This club no longer exists")
    return InviteOut(club_id=club.id, club_name=club.name)


@router.post("/clubs/{club_id}/join", response_model=MemberOut)
def join_club(
    club_id: int,
    payload: ClubJoin,
    db: Session = Depends(get_db),
    current_player: PlayerRow = Depends(get_current_player),
) -> MemberOut:
    """Joins the verified caller to a club, only with that club's invite
    token, so clubs cannot be joined by trying ids. Checked first, so
    a missing club and a missing token get the same answer.
    """
    if club_id_from_invite_token(payload.invite) != club_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Invite link not recognised")
    _get_club_or_404(db, club_id)

    existing = db.get(
        ClubMemberRow, {"club_id": club_id, "player_id": current_player.id}
    )
    if existing is not None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Already asked to join — waiting for the organizer"
            if existing.status == "pending"
            else "Already a member of this club",
        )

    # The link only gets you into the queue; the organizer lets you in.
    db.add(
        ClubMemberRow(
            club_id=club_id,
            player_id=current_player.id,
            role="member",
            joined_at=_now(),
            status="pending",
            wants_fixed_membership=payload.wants_fixed_membership,
        )
    )
    db.commit()
    return MemberOut(
        id=current_player.id,
        name=current_player.name,
        gender=_gender(current_player.gender),
        avatar_url=current_player.avatar_url,
        linked=current_player.line_user_id is not None,
    )


@router.get("/clubs/{club_id}/join-requests", response_model=list[ClubMemberOut])
def list_join_requests(
    club_id: int,
    db: Session = Depends(get_db),
    current_player: PlayerRow = Depends(get_current_player),
) -> list[ClubMemberOut]:
    """Who asked to join through the link and is waiting for the organizer."""
    _get_club_or_404(db, club_id)
    _require_organizer(db, club_id, current_player)
    rows = (
        db.query(PlayerRow, ClubMemberRow)
        .join(ClubMemberRow, ClubMemberRow.player_id == PlayerRow.id)
        .filter(ClubMemberRow.club_id == club_id, ClubMemberRow.status == "pending")
        .order_by(ClubMemberRow.joined_at)
        .all()
    )
    return [
        ClubMemberOut(
            id=player.id,
            name=player.name,
            gender=_gender(player.gender),
            avatar_url=player.avatar_url,
            linked=player.line_user_id is not None,
            role=membership.role,
            status=membership.status,
            wants_fixed_membership=membership.wants_fixed_membership,
        )
        for player, membership in rows
    ]


@router.post("/clubs/{club_id}/members/{player_id}/approve", response_model=MemberOut)
def approve_member(
    club_id: int,
    player_id: int,
    payload: JoinApproval,
    db: Session = Depends(get_db),
    current_player: PlayerRow = Depends(get_current_player),
) -> MemberOut:
    """Lets somebody who asked to join into the club. Putting them on a
    season's roster is a separate request, so money is worked out in
    one place.
    """
    _get_club_or_404(db, club_id)
    _require_organizer(db, club_id, current_player)
    membership = db.get(ClubMemberRow, {"club_id": club_id, "player_id": player_id})
    if membership is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not a member of this club")
    if membership.status != "pending":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Not waiting to be approved")
    membership.status = "active"
    membership.wants_fixed_membership = payload.as_fixed
    _audit(db, current_player, "member_approved", club_id, subject_id=player_id)
    db.commit()
    player = _get_player_or_404(db, player_id)
    return MemberOut(
        id=player.id,
        name=player.name,
        gender=_gender(player.gender),
        avatar_url=player.avatar_url,
        linked=player.line_user_id is not None,
    )


@router.put("/clubs/{club_id}/members/me/intent", response_model=MyClubOut)
def set_membership_intent(
    club_id: int,
    payload: MembershipIntent,
    db: Session = Depends(get_db),
    current_player: PlayerRow = Depends(get_current_player),
) -> MyClubOut:
    """A new member saying whether they want to be a fixed member or play
    single games.

    Wanting to be fixed queues them for the organizer, who decides
    what anybody owes; single games open drop-in signups at once. Self
    only, and changeable.
    """
    club = _get_club_or_404(db, club_id)
    membership = db.get(
        ClubMemberRow, {"club_id": club_id, "player_id": current_player.id}
    )
    if membership is None or membership.status != "active":
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "You are not a member of this club"
        )

    membership.wants_fixed_membership = payload.wants_fixed_membership
    db.commit()
    return MyClubOut(
        id=club.id,
        name=club.name,
        role=membership.role,
        wants_fixed_membership=membership.wants_fixed_membership,
    )
