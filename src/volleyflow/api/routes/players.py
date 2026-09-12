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
    _unique_display_name,
    get_current_player,
)
from volleyflow.api.schemas import (
    GenderUpdate,
    MemberOut,
    NameUpdate,
    PlayerIdentify,
    PlayerIdentifyOut,
)
from volleyflow.db.models import (
    PlayerRow,
)

router = APIRouter()


@router.post("/players/identify", response_model=PlayerIdentifyOut)
def identify_player(
    payload: PlayerIdentify, db: Session = Depends(get_db)
) -> PlayerIdentifyOut:
    """Called once per LIFF page load, right after LIFF resolves. The
    one endpoint that verifies a token itself rather than depending on
    get_current_player: there's no Player row to look up yet on a first
    visit, so identity has to come from the token directly. Two cases:

    1. This line_user_id has been seen before — this is a returning
       player. Sync their display name/avatar (LINE names can change).
    2. Never seen before — a genuinely new person. Create them; they
       exist but aren't a fixed member of any season until the organizer
       promotes them from a season's join-pool (see list_join_pool).

    Deliberately does not try to auto-claim an existing name-only Player
    by matching display_name — the organizer's own account of who's who
    is more trustworthy than a name-string guess, and a wrong guess
    would silently hand someone else's ledger history to a stranger.
    Reconciling a real person's pre-LIFF record with their LINE identity
    is a manual, organizer-driven action.
    """
    try:
        line_user_id = auth.verify_id_token(payload.id_token)
    except ValueError as e:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "Invalid or expired LINE ID token"
        ) from e

    player = db.query(PlayerRow).filter(PlayerRow.line_user_id == line_user_id).first()
    if player is not None:
        if player.name != payload.display_name:
            player.name = _unique_display_name(
                db, payload.display_name, exclude_player_id=player.id
            )
        player.avatar_url = payload.picture_url
        db.commit()
        db.refresh(player)
        return PlayerIdentifyOut(
            id=player.id,
            name=player.name,
            avatar_url=player.avatar_url,
            gender=_gender(player.gender),
        )

    name = _unique_display_name(db, payload.display_name)
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
    )


@router.put("/players/{player_id}/gender", response_model=MemberOut)
def set_player_gender(
    player_id: int,
    payload: GenderUpdate,
    db: Session = Depends(get_db),
    current_player: PlayerRow = Depends(get_current_player),
) -> MemberOut:
    """Self-reported by the player — never billing-relevant, only shown
    on the roster so a game's expected male/female split is visible.

    Yours to set, with one exception: a player the organizer typed in by
    hand has no LINE account, so they cannot open the app and set it
    themselves, and their half of the roster's male/female count would
    be stuck at unknown forever. An organizer of a club they belong to
    may fill it in for them. Once that person claims a LINE identity,
    it's theirs alone again.
    """
    player = _get_player_or_404(db, player_id)
    if current_player.id != player_id and not _may_edit_accountless_player(
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
    """What a player is called on the roster is theirs to set, same
    self-only reasoning as set_player_gender. Ledger entries and season
    membership key off player_id, never off this string, so renaming
    never touches billing history — CLAUDE.md 2.1's "one person, one
    name, for life" tracks the id; this is just the label. Runs through
    _unique_display_name so a rename can't collide with someone else's
    current name (excluding the player's own row, so keeping the name
    they already have is always allowed).
    """
    player = _get_player_or_404(db, player_id)
    if current_player.id != player_id and not _may_edit_accountless_player(
        db, current_player, player
    ):
        # Same rule as set_player_gender, which allowed this from the
        # start. Without it an organizer who mistypes a guest's name can
        # never correct it — the guest has no account to fix it from
        # themselves — while the gender on the very same row stays
        # editable, which is an arbitrary place to draw the line.
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "You can only rename yourself, or someone you added who has no account",
        )
    name = payload.name.strip()
    if not name:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Name can't be empty")
    player.name = _unique_display_name(db, name, exclude_player_id=player.id)
    db.commit()
    db.refresh(player)
    return MemberOut(
        id=player.id,
        name=player.name,
        gender=_gender(player.gender),
        avatar_url=player.avatar_url,
        linked=player.line_user_id is not None,
    )
