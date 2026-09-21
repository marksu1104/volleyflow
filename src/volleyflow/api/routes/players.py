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
    """Called once per LIFF page load, right after LIFF resolves. The
    one endpoint that verifies a token itself rather than depending on
    get_current_player: there's no Player row to look up yet on a first
    visit, so identity has to come from the token directly. Two cases:

    1. This line_user_id has been seen before — this is a returning
       player. Sync their avatar, but preserve the name they chose in
       VolleyFlow. LINE's display name is only the initial suggestion.
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
    """Whether LINE would actually deliver a push to the caller.

    Only ever about the caller themselves, and that is the design, not
    a limitation. The obvious alternative — reporting this for every
    organizer of a club — would tell organizer A something only
    organizer B can act on, while exposing B's account state to A for
    no benefit.

    Deliberately not folded into POST /players/identify, which runs on
    every LIFF page load and is otherwise pure database work. This
    makes an outbound call to LINE; putting it there would charge every
    page open a network round trip to answer a question only the
    management screen asks.

    `line_client` is imported as a module and called through it, so a
    test can replace is_reachable — the same reason `auth` is imported
    that way, and for the same failure if it isn't (tests/api/conftest).
    """
    if current_player.line_user_id is None:
        # A name-only player, added by hand and with no LINE account to
        # reach. get_current_player needs a token, so this is close to
        # unreachable in practice — but "no account" is not "not a
        # friend", and answering false would offer them a fix that
        # would not help.
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
    never touches billing history — the domain model's "one person, one
    name, for life" tracks the id; this is just the label. Runs through
    Names only need disambiguating inside clubs the player actively belongs
    to. A same-named person elsewhere in VolleyFlow is unrelated and must not
    force a global ``(2)`` suffix.
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
