"""A club's people: who is in it, guests, linking a LINE account, merging
duplicates, and removing somebody."""

import re
from typing import Any, Literal

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    status,
)
from sqlalchemy import func
from sqlalchemy.orm import Session

from volleyflow.api.dependencies import get_db
from volleyflow.api.routes._audit import _audit
from volleyflow.api.routes._people import (
    _MY_GUESTS_LIMIT,
    _gender,
    _get_club_or_404,
    _get_or_create_player,
    _get_player_or_404,
    _require_club_access,
    _require_club_member,
    _require_organizer,
    _today_in_taiwan,
    get_current_player,
)
from volleyflow.api.schemas import (
    ClubGuestAdd,
    ClubMemberOut,
    GuestOut,
    MemberOut,
    PlayerLink,
    PlayerMerge,
)
from volleyflow.db.models import (
    QUEUED,
    AbsenceRow,
    ClubMemberRow,
    DropInRow,
    GameRow,
    LedgerEntryRow,
    PlayerRow,
    SeasonMemberRow,
    SeasonRow,
)
from volleyflow.schedule import GameStatus

router = APIRouter()


@router.get("/clubs/{club_id}/members", response_model=list[ClubMemberOut])
def list_club_members(
    club_id: int,
    include: Literal["pending"] | None = Query(default=None),
    db: Session = Depends(get_db),
    current_player: PlayerRow = Depends(get_current_player),
) -> list[ClubMemberOut]:
    """Everyone in the club and their role — distinct from a *season's*
    fixed roster (GET /seasons/{id} returns that). Members only: this
    returns real names, genders and LINE profile pictures. Pending requests
    are only included when an organizer explicitly asks for them: ordinary
    club members must never learn who is waiting to join.
    """
    _get_club_or_404(db, club_id)
    statuses: tuple[str, ...]
    if include == "pending":
        _require_organizer(db, club_id, current_player)
        statuses = ("active", "pending")
    else:
        _require_club_access(db, club_id, current_player)
        statuses = ("active",)
    query = (
        db.query(PlayerRow, ClubMemberRow)
        .join(ClubMemberRow, ClubMemberRow.player_id == PlayerRow.id)
        .filter(ClubMemberRow.club_id == club_id, ClubMemberRow.status.in_(statuses))
    )
    rows = (
        query.order_by(
            ClubMemberRow.status, ClubMemberRow.joined_at, PlayerRow.id
        ).all()
        if include == "pending"
        else query.order_by(PlayerRow.id).all()
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


@router.post("/clubs/{club_id}/guests", response_model=MemberOut)
def add_club_guest(
    club_id: int,
    payload: ClubGuestAdd,
    db: Session = Depends(get_db),
    current_player: PlayerRow = Depends(get_current_player),
) -> MemberOut:
    """Add an accountless visitor to a club, but not to any season.

    A club roster and a season's fixed roster answer different questions:
    this route only records that the person belongs to the team. The
    organizer may later add them to a season through the existing season
    endpoint, which is the only path that creates a season-fee charge.
    """
    _get_club_or_404(db, club_id)
    _require_organizer(db, club_id, current_player)
    name = payload.name.strip()
    if not name:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Name can't be empty")
    existing = (
        db.query(PlayerRow)
        .join(ClubMemberRow, ClubMemberRow.player_id == PlayerRow.id)
        .filter(ClubMemberRow.club_id == club_id, PlayerRow.name == name)
        .first()
    )
    if existing is not None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "Already a member of this club"
        )
    player = _get_or_create_player(db, club_id, name)
    if payload.gender is not None:
        player.gender = payload.gender
    db.commit()
    db.refresh(player)
    return MemberOut(
        id=player.id,
        name=player.name,
        gender=_gender(player.gender),
        avatar_url=player.avatar_url,
        linked=False,
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
    # Places they held in a queue carry no money, so they go rather than
    # block the link; anything on court was charged and was refused above.
    db.query(DropInRow).filter(
        DropInRow.player_id == source.id, DropInRow.status == QUEUED
    ).delete(synchronize_session=False)
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
    # A live signup in either list: on court or queued, it is a game they
    # are down for.
    for table in (AbsenceRow, DropInRow):
        touched |= {
            game_id
            for (game_id,) in db.query(table.game_id).filter(
                table.player_id == player_id,
                table.cancelled_at.is_(None),
                table.game_id.in_(game_ids),
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
        (LedgerEntryRow, LedgerEntryRow.player_id, LedgerEntryRow.club_id == club_id),
    ]
    for table, column, in_this_club in moves:
        db.query(table).filter(column == duplicate.id, in_this_club).update(
            {column: keep.id}, synchronize_session=False
        )
    db.delete(duplicate_membership)
    if keep.gender is None:
        keep.gender = duplicate.gender
    # Repair the label produced by the old global collision rule while the
    # organizer is explicitly confirming that these two rows are one person.
    # A naturally numbered name is untouched unless it is exactly the old
    # row's name plus the generated suffix.
    if re.fullmatch(rf"{re.escape(duplicate.name)} \(\d+\)", keep.name):
        keep.name = duplicate.name
    _audit(
        db,
        current_player,
        "players_merged",
        club_id,
        subject_name=keep.name,
        detail=duplicate.name,
    )
    db.commit()
    return MemberOut(
        id=keep.id,
        name=keep.name,
        gender=_gender(keep.gender),
        avatar_url=keep.avatar_url,
        linked=keep.line_user_id is not None,
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

    # Taking somebody out while money or a signup is still open stranded
    # both: their fee could no longer be marked received, and their place
    # stayed on a roster under a name the club no longer had (2026-10-16).
    # Settled and finished with, they can go.
    balance = (
        db.query(func.coalesce(func.sum(LedgerEntryRow.amount), 0))
        .filter(
            LedgerEntryRow.club_id == club_id, LedgerEntryRow.player_id == player_id
        )
        .scalar()
    )
    if balance != 0:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Still has money to settle with this club — settle it before removing them",
        )
    signed_up = (
        db.query(DropInRow.id)
        .join(GameRow, GameRow.id == DropInRow.game_id)
        .join(SeasonRow, SeasonRow.id == GameRow.season_id)
        .filter(
            SeasonRow.club_id == club_id,
            DropInRow.player_id == player_id,
            DropInRow.cancelled_at.is_(None),
            GameRow.status == GameStatus.SCHEDULED,
            GameRow.date >= _today_in_taiwan(),
        )
        .first()
    )
    if signed_up is not None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Still signed up for a game to come — cancel that signup first",
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

    _audit(db, current_player, "member_removed", club_id, subject_id=player_id)
    db.delete(membership)
    db.commit()
