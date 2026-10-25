"""Who the caller is, and what they are allowed to do.

The bottom layer: nothing here knows about games, seasons or charges,
and it imports nothing else in this package, which keeps the
dependencies one-way.
"""

import os
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
    LedgerEntryRow,
    PlayerRow,
)

# How many previously brought people the quick picker offers; anyone else
# can still be typed in.
_MY_GUESTS_LIMIT = 12


def _now() -> datetime:
    """Server time, UTC, naive. Never trust a client-supplied time for
    anything that feeds FIFO ordering.
    """
    return datetime.now(UTC).replace(tzinfo=None)


def _get_or_create_player(db: Session, club_id: int, name: str) -> PlayerRow:
    """The player with this name in this club, created (with their club
    membership) if there is none. Names are unique within a club, not
    globally.
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
    db: Session,
    display_name: str,
    exclude_player_id: int | None = None,
    club_ids: list[int] | None = None,
) -> str:
    """Keeps a renamed player's name distinct within the clubs in
    `club_ids`, where some attendance routes still use the name as a
    key.

    Names are not unique globally, and a pending join request keeps
    its LINE name so the organizer can link it to an existing roster
    entry explicitly.
    """
    if not club_ids:
        return display_name
    candidate = display_name
    suffix = 2
    while True:
        query = (
            db.query(PlayerRow)
            .join(ClubMemberRow, ClubMemberRow.player_id == PlayerRow.id)
            .filter(
                ClubMemberRow.club_id.in_(club_ids),
                ClubMemberRow.status == "active",
                PlayerRow.name == candidate,
            )
        )
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


def _require_dealings_with_club(db: Session, club_id: int, player_id: int) -> None:
    """A member, or somebody with money on this club's books: a drop-in
    later removed from the club still owes for the nights they played.
    """
    if db.get(ClubMemberRow, {"club_id": club_id, "player_id": player_id}):
        return
    on_the_books = (
        db.query(LedgerEntryRow.id)
        .filter(
            LedgerEntryRow.club_id == club_id, LedgerEntryRow.player_id == player_id
        )
        .first()
    )
    if on_the_books is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "Player is not a member of this club"
        )


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
    """The verified caller, from the LINE ID token in the Authorization
    header. Never trust a player id sent in a request body for
    anything that changes data.

    404, not 401, when no Player exists for this LINE user yet: the
    caller never called POST /players/identify, the one endpoint that
    verifies a token itself.
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


def _runs_club(db: Session, club_id: int, player: PlayerRow) -> bool:
    """Whether `player` may act as this club's organizer: they organize
    it, or they are the developer, who may step into any club. Every
    organizer check goes through here.
    """
    if is_developer(player):
        return True
    membership = db.get(ClubMemberRow, {"club_id": club_id, "player_id": player.id})
    return membership is not None and membership.role == "organizer"


def _require_club_access(db: Session, club_id: int, current_player: PlayerRow) -> None:
    """Only members may read a club's roster, seasons and games: clubs
    never see each other's members, seasons or books.
    """
    if is_developer(current_player):
        return
    membership = db.get(
        ClubMemberRow, {"club_id": club_id, "player_id": current_player.id}
    )
    if membership is None:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "You are not a member of this club"
        )
    if membership.status != "active":
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "Waiting for the organizer to approve you"
        )


def _organizes_a_club_of(
    db: Session, current_player: PlayerRow, target: PlayerRow
) -> bool:
    """Whether `current_player` organizes any club `target` belongs to.
    Kept separate from _may_edit_accountless_player so loosening one
    rule cannot loosen the other.
    """
    if is_developer(current_player):
        return True
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


def _may_edit_accountless_player(
    db: Session, current_player: PlayerRow, target: PlayerRow
) -> bool:
    """Whether `current_player` organizes a club of `target`, and
    `target` has no LINE account to act for themselves: they exist
    only because somebody typed their name.
    """
    if target.line_user_id is not None:
        return False
    return _organizes_a_club_of(db, current_player, target)


def _require_organizer(db: Session, club_id: int, current_player: PlayerRow) -> None:
    if not _runs_club(db, club_id, current_player):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "Only this club's organizer can do that"
        )


def _require_self_or_organizer(
    db: Session, club_id: int, current_player: PlayerRow, target_player_id: int
) -> None:
    """A member may act on their own attendance and substitutes; the
    club's organizer may act on anyone's.
    """
    if current_player.id == target_player_id:
        return
    if not _runs_club(db, club_id, current_player):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "You can only do that for yourself, unless you're the organizer",
        )


def _require_may_sign_up(
    db: Session, club_id: int, current_player: PlayerRow, target: PlayerRow
) -> None:
    """A club member may sign up anyone without a LINE account (a friend
    they bring cannot act for themselves). Somebody with an account
    may only be signed up by themselves or the organizer: a drop-in
    costs money, and nobody may commit a real user to it.
    """
    if current_player.id == target.id:
        return
    if _runs_club(db, club_id, current_player):
        return
    membership = db.get(
        ClubMemberRow, {"club_id": club_id, "player_id": current_player.id}
    )
    if membership is None or membership.status != "active":
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
    """Narrows the plain `gender` column back to `Gender`; every write
    goes through Gender-typed input.
    """
    if value == "male" or value == "female":
        return cast(Gender, value)
    return None


_TAIWAN = timezone(timedelta(hours=8))


def _now_in_taiwan() -> datetime:
    """Wall-clock time where the clubs play, without a zone: the terms
    game dates and start times are stored in.
    """
    return datetime.now(UTC).astimezone(_TAIWAN).replace(tzinfo=None)


def _today_in_taiwan() -> date:
    """Today in the clubs' own time zone; UTC would turn the day over
    eight hours early.
    """
    return datetime.now(UTC).astimezone(_TAIWAN).date()


def is_developer(player: PlayerRow) -> bool:
    """Whether this person is the developer, set by
    `DEVELOPER_LINE_USER_ID` rather than a database role, so it cannot
    be granted through the app. Unset means nobody.
    """
    developer = os.environ.get("DEVELOPER_LINE_USER_ID")
    return bool(developer) and player.line_user_id == developer
