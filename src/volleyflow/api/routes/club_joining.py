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
    """The token for this club's join link — organizer-only, since only
    the join-link screen in organizer-members.html asks for it. See
    api/invites.py for why a token replaced the raw club id.
    """
    club = _get_club_or_404(db, club_id)
    _require_organizer(db, club_id, current_player)
    return InviteOut(club_id=club.id, club_name=club.name, token=invite_token(club.id))


@router.get("/invites/{token}", response_model=InviteOut)
def resolve_invite(token: str, db: Session = Depends(get_db)) -> InviteOut:
    """What a shared join link points at — the "加入「晴光館」？" prompt
    needs a name before anyone has joined or even signed in, which is
    also why this needs no caller identity: the token itself, not an
    account, is what makes a club findable here. Forging one is a
    64-bit HMAC guess, which is a stronger gate than the LINE login this
    replaced (see api/invites.py).
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
    """A player can only join a club as themselves — the verified
    caller, never an arbitrary player_id someone else could sign up — and
    only by holding that club's invite link.

    The token used to be optional here. `?invite=` hid the club's id from
    anyone reading a shared link, but this endpoint went on accepting a
    bare id from any signed-in caller, so trying small integers still
    joined every club in turn — and a member can read the whole roster.
    Left open on purpose while the app ran one club;
    closed on 2026-09-15, before other clubs were let
    in, when tests/api/test_tenant_isolation.py sent every route at one
    club as an outsider and this one let it straight in.

    Checked before anything else, so a club that doesn't exist and a club
    you have no link to get the same answer.
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
    """Lets somebody who asked to join into the club. Putting a fixed member
    on a season's roster is the next request, the one the roster screen uses,
    so the money is worked out in one place only."""
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
    """Someone who has just joined saying which kind of member they are.

    A fixed member's season fee is a real obligation, so claiming to be
    one can't put you on the roster by itself — it queues you for the
    organizer, who is the one deciding what anybody owes. Saying you're
    not opens up drop-in signups immediately, which commit you to one
    game at a time and nothing more.

    Self only, and always changeable: someone who dropped in all season
    and now wants in properly says so the same way.
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
