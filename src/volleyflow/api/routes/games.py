"""One night: cancelling it, and what the air conditioning did to it."""

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    status,
)
from sqlalchemy.orm import Session

from volleyflow.api.dependencies import get_db
from volleyflow.api.routes._attendance import (
    _get_game_or_404,
)
from volleyflow.api.routes._money import (
    _sync_season_fee_ledger,
)
from volleyflow.api.routes._people import (
    _require_organizer,
    get_current_player,
)
from volleyflow.api.schemas import (
    AirConditioningUpdate,
    GameCancel,
    GameOut,
)
from volleyflow.db.models import (
    PlayerRow,
    SeasonRow,
)
from volleyflow.schedule import GameStatus

router = APIRouter()


@router.post("/games/{game_id}/cancel", response_model=GameOut)
def cancel_game(
    game_id: int,
    payload: GameCancel,
    db: Session = Depends(get_db),
    current_player: PlayerRow = Depends(get_current_player),
) -> GameOut:
    """Calls off one scheduled game entirely — distinct from a member
    recording an absence, which leaves the game itself on. See
    docs/billing-rules.md "Game cancellation": `refunded=True`
    (CANCELLED_REFUNDED) lowers every current member's billable_games by
    one and credits share_per_game back to each of them right away;
    `refunded=False` (CANCELLED_UNREFUNDED) changes nobody's charge,
    since the venue cost was already paid either way. Existing
    absence/drop-in rows for this game are left alone — the refund
    calculation already ignores CANCELLED_REFUNDED games.
    """
    game = _get_game_or_404(db, game_id)
    season = db.get(SeasonRow, game.season_id)
    assert season is not None
    _require_organizer(db, season.club_id, current_player)
    if season.settled_at is not None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Season is already settled")
    if game.status != GameStatus.SCHEDULED:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Game is already cancelled")

    game.status = (
        GameStatus.CANCELLED_REFUNDED
        if payload.refunded
        else GameStatus.CANCELLED_UNREFUNDED
    )
    db.flush()
    if payload.refunded:
        _sync_season_fee_ledger(db, season)
    db.commit()
    db.refresh(game)
    return GameOut(id=game.id, date=game.date, status=game.status)


@router.put("/games/{game_id}/air-conditioning", response_model=GameOut)
def set_game_air_conditioning(
    game_id: int,
    payload: AirConditioningUpdate,
    db: Session = Depends(get_db),
    current_player: PlayerRow = Depends(get_current_player),
) -> GameOut:
    """Record whether the air conditioning actually ran for this game.

    Which nights get cooled is a forecast when the season is booked and
    a fact on the evening itself, so this is the one season parameter
    that is expected to change mid-season. Flipping it moves the club's
    real bill by `ac_surcharge` — the venue charges for the AC it ran —
    so `total_venue_cost` moves with it, and every member's charge is
    corrected by an adjustment entry rather than by editing what they
    were originally charged. Same machinery as changing the venue cost
    by hand; see _sync_season_fee_ledger and CLAUDE.md 2.4.

    A drop-in already charged for this game keeps the amount they were
    charged. They pay the organizer in cash on the night, and chasing
    someone for another $35 — or handing it back — because the AC
    decision changed is worse than the small unfairness of leaving it.
    The members absorb the difference, which is what the season fee is
    for.
    """
    game = _get_game_or_404(db, game_id)
    season = db.get(SeasonRow, game.season_id)
    assert season is not None  # game.season_id is a foreign key, always valid
    _require_organizer(db, season.club_id, current_player)
    if season.settled_at is not None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Season is already settled")

    if game.air_conditioned == payload.air_conditioned:
        return GameOut(id=game.id, date=game.date, status=game.status)

    delta = season.ac_surcharge if payload.air_conditioned else -season.ac_surcharge
    if season.total_venue_cost + delta < 0:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "That would make the season's venue cost negative — "
            "check the air-conditioning surcharge",
        )

    game.air_conditioned = payload.air_conditioned
    season.total_venue_cost += delta
    db.flush()
    _sync_season_fee_ledger(db, season)
    db.commit()
    db.refresh(game)
    return GameOut(id=game.id, date=game.date, status=game.status)
