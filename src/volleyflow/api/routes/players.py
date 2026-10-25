"""One person: their LINE identity, their name, their gender."""

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    status,
)
from sqlalchemy.orm import Session

from volleyflow.api import auth
from volleyflow.api.dependencies import get_db
from volleyflow.api.routes._people import (
    _gender,
    _get_player_or_404,
    _may_edit_accountless_player,
    _organizes_a_club_of,
    _unique_display_name,
    get_current_player,
    is_developer,
)
from volleyflow.api.schemas import (
    GenderUpdate,
    LineReachableOut,
    MemberOut,
    NameUpdate,
    PlayerIdentify,
    PlayerIdentifyOut,
)
from volleyflow.db.models import (
    ClubMemberRow,
    PlayerRow,
)
from volleyflow.notify import line_client

router = APIRouter()


@router.post("/players/identify", response_model=PlayerIdentifyOut)
def identify_player(
    payload: PlayerIdentify, db: Session = Depends(get_db)
) -> PlayerIdentifyOut:
    """Called on every LIFF page load. The one endpoint that verifies the
    ID token itself, since a first visit has no Player yet.

    A known LINE user gets their avatar refreshed and keeps the name
    they chose in the app. A new one is created, belonging to no
    season until the organizer adds them. Never matched to an existing
    typed-in player by name: a wrong guess would hand somebody's
    ledger to a stranger, so linking is the organizer's decision.
    """
    try:
        line_user_id = auth.verify_id_token(payload.id_token)
    except ValueError as e:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "Invalid or expired LINE ID token"
        ) from e

    player = db.query(PlayerRow).filter(PlayerRow.line_user_id == line_user_id).first()
    if player is not None:
        player.avatar_url = payload.picture_url
        db.commit()
        db.refresh(player)
        return PlayerIdentifyOut(
            id=player.id,
            name=player.name,
            avatar_url=player.avatar_url,
            gender=_gender(player.gender),
            is_developer=is_developer(player),
        )

    name = payload.display_name.strip()
    if not name:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Name can't be empty")
    new_player = PlayerRow(
        name=name, line_user_id=line_user_id, avatar_url=payload.picture_url
    )
    db.add(new_player)
    db.commit()
    db.refresh(new_player)
    return PlayerIdentifyOut(
        id=new_player.id,
        name=new_player.name,
        avatar_url=new_player.avatar_url,
        gender=_gender(new_player.gender),
        is_developer=is_developer(new_player),
    )


@router.get("/players/me/line-reachable", response_model=LineReachableOut)
def check_line_reachable(
    current_player: PlayerRow = Depends(get_current_player),
) -> LineReachableOut:
    """Whether LINE would deliver a push to the caller (whether they
    added the Official Account).

    Kept out of POST /players/identify, which runs on every page load,
    because it calls LINE. `line_client` is called through the module
    so tests can replace it.
    """
    if current_player.line_user_id is None:
        # No LINE account: "cannot be reached" is not "has not added the
        # friend", so the answer is unknown, not false.
        return LineReachableOut(reachable=None)
    return LineReachableOut(
        reachable=line_client.is_reachable(current_player.line_user_id)
    )


@router.put("/players/{player_id}/gender", response_model=MemberOut)
def set_player_gender(
    player_id: int,
    payload: GenderUpdate,
    db: Session = Depends(get_db),
    current_player: PlayerRow = Depends(get_current_player),
) -> MemberOut:
    """Sets a player's gender, shown on the roster; never used by
    billing. The player may set their own, and an organizer may set
    anyone's in their club.
    """
    player = _get_player_or_404(db, player_id)
    if current_player.id != player_id and not _organizes_a_club_of(
        db, current_player, player
    ):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "You can only set your own gender"
        )
    player.gender = payload.gender
    db.commit()
    db.refresh(player)
    return MemberOut(
        id=player.id,
        name=player.name,
        gender=_gender(player.gender),
        avatar_url=player.avatar_url,
        linked=player.line_user_id is not None,
    )


@router.put("/players/{player_id}/name", response_model=MemberOut)
def set_player_name(
    player_id: int,
    payload: NameUpdate,
    db: Session = Depends(get_db),
    current_player: PlayerRow = Depends(get_current_player),
) -> MemberOut:
    """Renames a player. Billing keys off the player id, never the name.
    A player may rename themselves, and an organizer may rename
    somebody without a LINE account. Names are kept distinct only
    within the clubs the player belongs to.
    """
    player = _get_player_or_404(db, player_id)
    if current_player.id != player_id and not _may_edit_accountless_player(
        db, current_player, player
    ):
        # An organizer may rename somebody without an account, who cannot
        # fix it themselves.
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "You can only rename yourself, or someone you added who has no account",
        )
    name = payload.name.strip()
    if not name:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Name can't be empty")
    club_ids = [
        club_id
        for (club_id,) in db.query(ClubMemberRow.club_id).filter(
            ClubMemberRow.player_id == player.id,
            ClubMemberRow.status == "active",
        )
    ]
    player.name = _unique_display_name(
        db,
        name,
        exclude_player_id=player.id,
        club_ids=club_ids,
    )
    db.commit()
    db.refresh(player)
    return MemberOut(
        id=player.id,
        name=player.name,
        gender=_gender(player.gender),
        avatar_url=player.avatar_url,
        linked=player.line_user_id is not None,
    )
