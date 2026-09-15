"""Clubs, their membership, and the invite link into one."""

from typing import Any

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    status,
)
from sqlalchemy import func
from sqlalchemy.orm import Session

from volleyflow.api.dependencies import get_db
from volleyflow.api.invites import club_id_from_invite_token, invite_token
from volleyflow.api.routes._attendance import (
    _delete_season_rows,
)
from volleyflow.api.routes._people import (
    _MY_GUESTS_LIMIT,
    _gender,
    _get_club_or_404,
    _get_player_or_404,
    _now,
    _require_club_access,
    _require_club_member,
    _require_organizer,
    get_current_player,
)
from volleyflow.api.schemas import (
    ClubCreate,
    ClubJoin,
    ClubMemberOut,
    ClubOut,
    ClubUpdate,
    GuestOut,
    InviteOut,
    JoinApproval,
    MemberOut,
    MembershipIntent,
    MyClubOut,
    PlayerLink,
    PlayerMerge,
)
from volleyflow.db.models import (
    AbsenceRow,
    ClubMemberRow,
    ClubRow,
    DropInRow,
    GameRow,
    LedgerEntryRow,
    PlayerRow,
    SeasonMemberRow,
    SeasonRow,
    WaitlistEntryRow,
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
    return [ClubOut(id=c.id, name=c.name, role=role) for c, role in rows]


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
    return [
        MyClubOut(
            id=club.id,
            name=club.name,
            role=membership.role,
            wants_fixed_membership=membership.wants_fixed_membership,
            status=membership.status,
            pending_count=waiting.get(club.id, 0),
        )
        for club, membership in rows
    ]


@router.get("/clubs/{club_id}/my-guests", response_model=list[GuestOut])
def list_my_guests(
    club_id: int,
    db: Session = Depends(get_db),
    current_player: PlayerRow = Depends(get_current_player),
) -> list[GuestOut]:
    """The people this caller has brought to this club before, most
    recent first.

    Typing a friend's name again every week is how the same person ends
    up in the database three times under three spellings, and each of
    those copies carries its own money. Offering the ones already
    brought — as a tap, not a text field — is what stops that.

    Deliberately capped and deduplicated by player: a club that brings in
    a lot of outside players would otherwise grow a list nobody can scan,
    and the useful part is the handful of regulars. Cancelled signups
    still count — somebody you brought and then couldn't is still
    somebody you know.
    """
    _require_club_access(db, club_id, current_player)

    rows = (
        db.query(PlayerRow, DropInRow.signed_up_at)
        .join(DropInRow, DropInRow.player_id == PlayerRow.id)
        .join(GameRow, GameRow.id == DropInRow.game_id)
        .join(SeasonRow, SeasonRow.id == GameRow.season_id)
        # Only people still in the club: picking a removed one can only fail.
        .join(
            ClubMemberRow,
            (ClubMemberRow.player_id == PlayerRow.id)
            & (ClubMemberRow.club_id == club_id),
        )
        .filter(
            SeasonRow.club_id == club_id,
            DropInRow.brought_by_player_id == current_player.id,
            PlayerRow.id != current_player.id,
        )
        .order_by(DropInRow.signed_up_at.desc())
        .all()
    )

    # Grouped by name, not by player id.
    #
    # A typed name always creates a new person — deliberately, since two
    # real people can be called 小明 and one inheriting the other's
    # ledger is not recoverable. The cost is that bringing the same
    # friend three weeks running, by typing, made three of them. Showing
    # that as three identical rows makes the picker useless at exactly
    # the moment it is meant to help.
    #
    # So they merge here, and the row carries the most recent id: picking
    # it signs the friend up as that person, which pulls future weeks
    # onto one row instead of adding a fourth. The list narrows itself
    # over time rather than growing.
    #
    # It cannot separate two different people who share a name — but
    # neither could the reader, and the manual field is still there for
    # somebody genuinely new.
    by_name: dict[str, GuestOut] = {}
    for player, signed_up_at in rows:
        existing = by_name.get(player.name)
        if existing is None:
            by_name[player.name] = GuestOut(
                id=player.id,
                name=player.name,
                gender=_gender(player.gender),
                times=1,
                last_played=signed_up_at.date(),
            )
        else:
            existing.times += 1
            if existing.gender is None:
                existing.gender = _gender(player.gender)
    return list(by_name.values())[:_MY_GUESTS_LIMIT]


@router.get("/clubs/{club_id}/members", response_model=list[ClubMemberOut])
def list_club_members(
    club_id: int,
    db: Session = Depends(get_db),
    current_player: PlayerRow = Depends(get_current_player),
) -> list[ClubMemberOut]:
    """Everyone in the club and their role — distinct from a *season's*
    fixed roster (GET /seasons/{id} returns that). Members only: this
    returns real names, genders and LINE profile pictures.
    """
    _get_club_or_404(db, club_id)
    _require_club_access(db, club_id, current_player)
    rows = (
        db.query(PlayerRow, ClubMemberRow)
        .join(ClubMemberRow, ClubMemberRow.player_id == PlayerRow.id)
        .filter(ClubMemberRow.club_id == club_id, ClubMemberRow.status == "active")
        .order_by(PlayerRow.id)
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
            wants_fixed_membership=membership.wants_fixed_membership,
        )
        for player, membership in rows
    ]


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
    db.commit()
    player = _get_player_or_404(db, player_id)
    return MemberOut(
        id=player.id,
        name=player.name,
        gender=_gender(player.gender),
        avatar_url=player.avatar_url,
        linked=player.line_user_id is not None,
    )


@router.post("/clubs/{club_id}/players/{player_id}/link", response_model=MemberOut)
def link_player(
    club_id: int,
    player_id: int,
    payload: PlayerLink,
    db: Session = Depends(get_db),
    current_player: PlayerRow = Depends(get_current_player),
) -> MemberOut:
    """Says "this LINE account is that person on the roster".

    identify_player deliberately never guesses this by name: a wrong
    guess hands someone else's ledger to a stranger, so the organizer —
    who actually knows who's who — makes the call. That was always the
    design; this is the endpoint that was missing to carry it out, which
    is why a member typed in by hand stayed marked 訪客 even after they
    logged in, with a second copy of themselves sitting in the join pool.

    The roster entry survives, keeping its id and therefore its whole
    ledger history; the LINE-created row donates its identity and is
    deleted. Refused if that row has any history of its own, since
    deleting it would then destroy real records — remove the duplicate
    roster entry instead.
    """
    _get_club_or_404(db, club_id)
    _require_organizer(db, club_id, current_player)

    target = _get_player_or_404(db, player_id)
    source = _get_player_or_404(db, payload.line_player_id)
    if target.id == source.id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Those are the same player")
    _require_club_member(db, club_id, target.id)
    _require_club_member(db, club_id, source.id)

    if target.line_user_id is not None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "That roster entry is already linked to a LINE account",
        )
    if source.line_user_id is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "That player has no LINE account to link",
        )

    for model, column, what in (
        (LedgerEntryRow, LedgerEntryRow.player_id, "ledger entries"),
        (SeasonMemberRow, SeasonMemberRow.player_id, "season memberships"),
        (AbsenceRow, AbsenceRow.player_id, "absences"),
        (DropInRow, DropInRow.player_id, "drop-ins"),
    ):
        if db.query(model).filter(column == source.id).first() is not None:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"That LINE account already has {what} of its own — "
                "remove the duplicate roster entry instead",
            )

    # Two flushes, not one: line_user_id is unique, and within a single
    # flush SQLAlchemy is free to apply the target's UPDATE before the
    # source's, leaving both rows holding the same id for an instant —
    # which the index rejects. Release it first, then hand it over.
    released = source.line_user_id
    source.line_user_id = None
    db.flush()
    target.line_user_id = released
    target.avatar_url = source.avatar_url
    db.flush()

    # Guests they brought come with them. This is the one reference to a
    # player that the refusal above doesn't cover, and it can't: signing a
    # friend up is an ordinary thing to have done before the organizer
    # gets round to linking your account, so refusing over it would block
    # the normal case. Re-pointed rather than cleared, because the whole
    # meaning of this endpoint is that the two rows are one person — and
    # left alone it was a foreign key violation, a 500, and a browser
    # reporting it as a CORS error because a crash carries no headers.
    db.query(DropInRow).filter(DropInRow.brought_by_player_id == source.id).update(
        {DropInRow.brought_by_player_id: target.id}, synchronize_session=False
    )
    db.query(WaitlistEntryRow).filter(WaitlistEntryRow.player_id == source.id).delete(
        synchronize_session=False
    )
    db.query(ClubMemberRow).filter(ClubMemberRow.player_id == source.id).delete(
        synchronize_session=False
    )
    db.flush()
    db.delete(source)
    db.commit()
    db.refresh(target)

    return MemberOut(
        id=target.id,
        name=target.name,
        gender=_gender(target.gender),
        avatar_url=target.avatar_url,
        linked=True,
    )


def _games_touched(
    db: Session, player_id: int, season_ids: list[int], game_ids: list[int]
) -> set[int]:
    """Every game in the club this person is down for, in any role."""
    rostered = [
        season_id
        for (season_id,) in db.query(SeasonMemberRow.season_id).filter(
            SeasonMemberRow.player_id == player_id,
            SeasonMemberRow.season_id.in_(season_ids),
        )
    ]
    touched = {
        game_id
        for (game_id,) in db.query(GameRow.id).filter(GameRow.season_id.in_(rostered))
    }
    for table in (AbsenceRow, DropInRow):
        touched |= {
            game_id
            for (game_id,) in db.query(table.game_id).filter(
                table.player_id == player_id,
                table.cancelled_at.is_(None),
                table.game_id.in_(game_ids),
            )
        }
    touched |= {
        game_id
        for (game_id,) in db.query(WaitlistEntryRow.game_id).filter(
            WaitlistEntryRow.player_id == player_id,
            WaitlistEntryRow.game_id.in_(game_ids),
        )
    }
    return touched


@router.post("/clubs/{club_id}/players/{player_id}/merge", response_model=MemberOut)
def merge_players(
    club_id: int,
    player_id: int,
    payload: PlayerMerge,
    db: Session = Depends(get_db),
    current_player: PlayerRow = Depends(get_current_player),
) -> MemberOut:
    """Folds a typed-in duplicate into the person they really are: every
    signup, absence and ledger entry in this club moves over, and the
    duplicate leaves the club. Refused where both are down for the same
    game, because one person can't be there twice."""
    _get_club_or_404(db, club_id)
    _require_organizer(db, club_id, current_player)
    keep = _get_player_or_404(db, player_id)
    duplicate = _get_player_or_404(db, payload.duplicate_id)
    if keep.id == duplicate.id:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "Pick two different people to merge"
        )
    kept_membership = db.get(ClubMemberRow, {"club_id": club_id, "player_id": keep.id})
    duplicate_membership = db.get(
        ClubMemberRow, {"club_id": club_id, "player_id": duplicate.id}
    )
    if kept_membership is None or duplicate_membership is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not a member of this club")
    if duplicate.line_user_id is not None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Only a name typed in by hand can be merged into someone else",
        )

    season_ids = [
        s for (s,) in db.query(SeasonRow.id).filter(SeasonRow.club_id == club_id)
    ]
    game_ids = [
        g for (g,) in db.query(GameRow.id).filter(GameRow.season_id.in_(season_ids))
    ]
    if _games_touched(db, keep.id, season_ids, game_ids) & _games_touched(
        db, duplicate.id, season_ids, game_ids
    ):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Both are down for the same game — sort that game out first",
        )

    moves: list[tuple[Any, Any, Any]] = [
        (
            SeasonMemberRow,
            SeasonMemberRow.player_id,
            SeasonMemberRow.season_id.in_(season_ids),
        ),
        (AbsenceRow, AbsenceRow.player_id, AbsenceRow.game_id.in_(game_ids)),
        (DropInRow, DropInRow.player_id, DropInRow.game_id.in_(game_ids)),
        (DropInRow, DropInRow.brought_by_player_id, DropInRow.game_id.in_(game_ids)),
        (
            WaitlistEntryRow,
            WaitlistEntryRow.player_id,
            WaitlistEntryRow.game_id.in_(game_ids),
        ),
        (
            WaitlistEntryRow,
            WaitlistEntryRow.brought_by_player_id,
            WaitlistEntryRow.game_id.in_(game_ids),
        ),
        (LedgerEntryRow, LedgerEntryRow.player_id, LedgerEntryRow.club_id == club_id),
    ]
    for table, column, in_this_club in moves:
        db.query(table).filter(column == duplicate.id, in_this_club).update(
            {column: keep.id}, synchronize_session=False
        )
    db.delete(duplicate_membership)
    if keep.gender is None:
        keep.gender = duplicate.gender
    db.commit()
    return MemberOut(
        id=keep.id,
        name=keep.name,
        gender=_gender(keep.gender),
        avatar_url=keep.avatar_url,
        linked=keep.line_user_id is not None,
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


@router.delete("/clubs/{club_id}/members/{player_id}", status_code=204)
def remove_club_member(
    club_id: int,
    player_id: int,
    db: Session = Depends(get_db),
    current_player: PlayerRow = Depends(get_current_player),
) -> None:
    """Take someone out of the club — a wrong-link join, a test account,
    or someone who has left for good. Distinct from removing them from a
    *season's* roster (DELETE /seasons/{id}/members/{id}), which is about
    one season's billing.

    Refused while they're still on any of this club's season rosters:
    that removal has to go through the season endpoint, which corrects
    everyone's charge. Silently dropping them here would leave a season
    whose member list and ledger disagree.

    Refused for the last organizer, which would leave the club with
    nobody able to manage it.
    """
    _get_club_or_404(db, club_id)
    _require_organizer(db, club_id, current_player)

    membership = db.get(ClubMemberRow, {"club_id": club_id, "player_id": player_id})
    if membership is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not a member of this club")

    on_a_roster = (
        db.query(SeasonMemberRow)
        .join(SeasonRow, SeasonRow.id == SeasonMemberRow.season_id)
        .filter(
            SeasonRow.club_id == club_id,
            SeasonMemberRow.player_id == player_id,
        )
        .first()
    )
    if on_a_roster is not None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Still a fixed member of a season — remove them from that season first",
        )

    if membership.role == "organizer":
        other_organizers = (
            db.query(ClubMemberRow)
            .filter(
                ClubMemberRow.club_id == club_id,
                ClubMemberRow.role == "organizer",
                ClubMemberRow.player_id != player_id,
            )
            .count()
        )
        if other_organizers == 0:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "This is the club's only organizer",
            )

    db.delete(membership)
    db.commit()


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
    club = db.get(ClubRow, club_id)
    if club is not None:
        db.delete(club)
    db.commit()
