"""Who the caller is, and what they are allowed to do.

The bottom layer: nothing here knows about a game, a season or a
charge. It imports nothing else in this package, which is what keeps
the dependency one-way.
"""

from datetime import UTC, date, datetime, timedelta, timezone
from typing import cast

from fastapi import (
    Depends,
    Header,
    HTTPException,
    status,
)
from sqlalchemy.orm import Session

from volleyflow.api import auth
from volleyflow.api.dependencies import get_db
from volleyflow.api.schemas import (
    Gender,
)
from volleyflow.db.models import (
    ClubMemberRow,
    ClubRow,
    PlayerRow,
)

# How many previously-brought people the quick picker offers. Long enough
# for the regulars, short enough to scan on a phone — the tail is
# one-off visitors nobody will pick again, and a text field is still
# there for anyone not on the list.
_MY_GUESTS_LIMIT = 12


def _now() -> datetime:
    """Server-assigned, UTC, naive — never trust a client-supplied time
    for anything that feeds FIFO ordering (see settlement.py).
    """
    return datetime.now(UTC).replace(tzinfo=None)


def _get_or_create_player(db: Session, club_id: int, name: str) -> PlayerRow:
    """One Player per name within a club, not globally — the same name
    in two different clubs is two different people (see CLAUDE.md 2.5:
    Player is global, but name has no uniqueness constraint of its own
    any more; ClubMembership is what's scoped). Creates the
    ClubMemberRow too when this is a brand new player, so this is the
    one place a name-typed player both exists and belongs to the club
    at the same time.
    """
    player = (
        db.query(PlayerRow)
        .join(ClubMemberRow, ClubMemberRow.player_id == PlayerRow.id)
        .filter(ClubMemberRow.club_id == club_id, PlayerRow.name == name)
        .first()
    )
    if player is None:
        player = PlayerRow(name=name)
        db.add(player)
        db.flush()  # assigns player.id without ending the transaction
        db.add(
            ClubMemberRow(
                club_id=club_id, player_id=player.id, role="member", joined_at=_now()
            )
        )
    return player


def _unique_display_name(
    db: Session, display_name: str, exclude_player_id: int | None = None
) -> str:
    """Two different LINE accounts can share a display name — since
    identify_player operates globally, not per club (a Player's LINE
    identity isn't club-scoped), a collision here isn't even between two
    people in the same club necessarily. Disambiguate with a numeric
    suffix rather than fail the request. exclude_player_id lets a
    returning player keep their own current name without tripping over
    themselves.
    """
    candidate = display_name
    suffix = 2
    while True:
        query = db.query(PlayerRow).filter(PlayerRow.name == candidate)
        if exclude_player_id is not None:
            query = query.filter(PlayerRow.id != exclude_player_id)
        if query.first() is None:
            return candidate
        candidate = f"{display_name} ({suffix})"
        suffix += 1


def _get_player_by_name(db: Session, club_id: int, name: str) -> PlayerRow:
    player = (
        db.query(PlayerRow)
        .join(ClubMemberRow, ClubMemberRow.player_id == PlayerRow.id)
        .filter(ClubMemberRow.club_id == club_id, PlayerRow.name == name)
        .first()
    )
    if player is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, f"No player named {name!r} in this club"
        )
    return player


def _get_player_or_404(db: Session, player_id: int) -> PlayerRow:
    player = db.get(PlayerRow, player_id)
    if player is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No player with id {player_id}")
    return player


def _get_club_or_404(db: Session, club_id: int) -> ClubRow:
    club = db.get(ClubRow, club_id)
    if club is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No club with id {club_id}")
    return club


def _require_club_member(db: Session, club_id: int, player_id: int) -> None:
    membership = db.get(ClubMemberRow, {"club_id": club_id, "player_id": player_id})
    if membership is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "Player is not a member of this club"
        )


def get_current_player(
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> PlayerRow:
    """The verified caller, from a LINE ID token in the Authorization
    header — never trust a client-supplied player_id/line_user_id in a
    request body for anything that changes data. Every mutating
    endpoint below takes this as a dependency instead.

    404s (not 401) if there's no Player for this line_user_id yet: that
    can only mean the caller never called POST /players/identify, which
    is the one endpoint that verifies a token itself and doesn't depend
    on this — everything else assumes identify already ran once.
    """
    if authorization is None or not authorization.startswith("Bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing bearer token")
    token = authorization.removeprefix("Bearer ")
    try:
        line_user_id = auth.verify_id_token(token)
    except ValueError as e:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(e)) from e

    player = db.query(PlayerRow).filter(PlayerRow.line_user_id == line_user_id).first()
    if player is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "No player identified for this LINE account yet"
        )
    return player


def _require_club_access(db: Session, club_id: int, current_player: PlayerRow) -> None:
    """You may read a club's roster, seasons and games only if you belong
    to it. CLAUDE.md 2.5: "clubs never see each other's members, seasons,
    or books." These reads were public until now, which meant every
    member's name, gender and LINE profile picture — plus who took leave
    and who dropped in — were readable by anyone who knew a club or
    season id, and GET /clubs handed out the ids.
    """
    membership = db.get(
        ClubMemberRow, {"club_id": club_id, "player_id": current_player.id}
    )
    if membership is None:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "You are not a member of this club"
        )


def _may_edit_accountless_player(
    db: Session, current_player: PlayerRow, target: PlayerRow
) -> bool:
    """Whether `current_player` organizes a club that `target` belongs to,
    and `target` has no LINE identity to speak for themselves with. The
    narrow case where acting on someone else's profile is legitimate:
    they only exist because an organizer typed their name in.
    """
    if target.line_user_id is not None:
        return False
    shared = (
        db.query(ClubMemberRow)
        .join(
            ClubRow,
            ClubRow.id == ClubMemberRow.club_id,
        )
        .filter(ClubMemberRow.player_id == target.id)
        .all()
    )
    for membership in shared:
        mine = db.get(
            ClubMemberRow,
            {"club_id": membership.club_id, "player_id": current_player.id},
        )
        if mine is not None and mine.role == "organizer":
            return True
    return False


def _require_organizer(db: Session, club_id: int, current_player: PlayerRow) -> None:
    membership = db.get(
        ClubMemberRow, {"club_id": club_id, "player_id": current_player.id}
    )
    if membership is None or membership.role != "organizer":
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "Only this club's organizer can do that"
        )


def _require_self_or_organizer(
    db: Session, club_id: int, current_player: PlayerRow, target_player_id: int
) -> None:
    """Lets a member act on their own attendance/substitute, and lets
    the club's organizer act on anyone's — the same "self, or the
    organizer" shape CLAUDE.md 2.3/2.4 describes for absences, signups,
    and substitutes throughout.
    """
    if current_player.id == target_player_id:
        return
    membership = db.get(
        ClubMemberRow, {"club_id": club_id, "player_id": current_player.id}
    )
    if membership is None or membership.role != "organizer":
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "You can only do that for yourself, unless you're the organizer",
        )


def _require_may_sign_up(
    db: Session, club_id: int, current_player: PlayerRow, target: PlayerRow
) -> None:
    """Signing up is deliberately looser than the rest of
    _require_self_or_organizer, because "+1, I'm bringing a friend" is
    how drop-ins actually happen — the friend isn't in LINE, has no way
    to tap anything, and the member bringing them is the one who pays
    and vouches for them.

    So a club member may sign up anyone who has no LINE identity of
    their own, on the same reasoning as _may_edit_accountless_player:
    an accountless player exists only because somebody typed their
    name, and can't act for themselves. Signing up someone who *does*
    have an account stays restricted to that person or the organizer —
    a drop-in costs money, and nobody may commit a real user to it.
    """
    if current_player.id == target.id:
        return
    membership = db.get(
        ClubMemberRow, {"club_id": club_id, "player_id": current_player.id}
    )
    if membership is None:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "You are not a member of this club"
        )
    if membership.role == "organizer" or target.line_user_id is None:
        return
    raise HTTPException(
        status.HTTP_403_FORBIDDEN,
        "That person has their own account — they need to sign themselves up",
    )


def _gender(value: str | None) -> Gender | None:
    """`PlayerRow.gender` is a plain column; every write to it goes
    through `Gender`-typed input (GenderUpdate, SubstituteCreate), so
    this narrows the read side back to that same type for callers.
    """
    if value == "male" or value == "female":
        return cast(Gender, value)
    return None


_TAIWAN = timezone(timedelta(hours=8))


def _today_in_taiwan() -> date:
    """A change deadline is about calendar days from the group's own
    perspective, not the server's UTC clock — using UTC for "today"
    would flip the day boundary 8 hours too early every night.
    """
    return datetime.now(UTC).astimezone(_TAIWAN).date()
