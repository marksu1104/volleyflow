"""One night: cancelling it, and what the air conditioning did to it."""

from decimal import Decimal

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
from volleyflow.api.routes._audit import _audit
from volleyflow.api.routes._money import (
    _sync_season_fee_ledger,
)
from volleyflow.api.routes._people import (
    _require_organizer,
    _today_in_taiwan,
    get_current_player,
)
from volleyflow.api.schemas import (
    AirConditioningUpdate,
    GameCancel,
    GameOut,
    GameUpdate,
)
from volleyflow.db.models import (
    GameRow,
    PlayerRow,
    SeasonRow,
)
from volleyflow.notify.reminders import notify_game_cancelled
from volleyflow.schedule import GameStatus

router = APIRouter()


@router.post("/games/{game_id}/cancel", response_model=GameOut)
def cancel_game(
    game_id: int,
    payload: GameCancel,
    db: Session = Depends(get_db),
    current_player: PlayerRow = Depends(get_current_player),
) -> GameOut:
    """Calls off one scheduled game. `refunded=True`
    (CANCELLED_REFUNDED): the venue returned the cost, so every member
    is credited that game's share. `refunded=False`
    (CANCELLED_UNREFUNDED): nobody's charge changes. Absences and
    signups for the game stay; billing ignores refunded games. See
    docs/billing-rules.md "Game cancellation".
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
    # A refunded cancellation returns this night's extra venue cost too, so
    # the season total drops by it. An unrefunded one keeps it: that court
    # was paid for.
    if payload.refunded and game.venue_cost_delta != 0:
        season.total_venue_cost -= game.venue_cost_delta
        game.venue_cost_delta = Decimal("0")
    db.flush()
    if payload.refunded:
        _sync_season_fee_ledger(db, season)
    _audit(
        db,
        current_player,
        "game_cancelled_refunded" if payload.refunded else "game_cancelled",
        season.club_id,
        detail=game.date.isoformat(),
    )
    db.commit()
    db.refresh(game)
    # After the commit: a message cannot be taken back.
    notify_game_cancelled(db, game)
    return GameOut(id=game.id, date=game.date, status=game.status)


@router.put("/games/{game_id}/air-conditioning", response_model=GameOut)
def set_game_air_conditioning(
    game_id: int,
    payload: AirConditioningUpdate,
    db: Session = Depends(get_db),
    current_player: PlayerRow = Depends(get_current_player),
) -> GameOut:
    """Records whether the air conditioning actually ran for this game.

    The venue charges for what it ran, so `total_venue_cost` moves by
    `ac_surcharge` and every member's charge is corrected with an
    adjustment entry. A drop-in already charged for the night keeps
    what they paid; members absorb the difference.
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
    _audit(
        db,
        current_player,
        "ac_on" if payload.air_conditioned else "ac_off",
        season.club_id,
        amount=delta,
        detail=game.date.isoformat(),
    )
    db.commit()
    db.refresh(game)
    return GameOut(id=game.id, date=game.date, status=game.status)


@router.patch("/games/{game_id}", response_model=GameOut)
def update_game(
    game_id: int,
    payload: GameUpdate,
    db: Session = Depends(get_db),
    current_player: PlayerRow = Depends(get_current_player),
) -> GameOut:
    """Changes one game's date, venue or time, keeping everything
    recorded against it (absences, signups and the queue belong to the
    game, not its date).

    Only fields sent are touched; `location` sent as null puts the
    night back on the season's venue. The date is refused for a
    settled season, a cancelled game, a date another game already has,
    and a date in the past.
    """
    game = _get_game_or_404(db, game_id)
    season = db.get(SeasonRow, game.season_id)
    assert season is not None
    _require_organizer(db, season.club_id, current_player)
    if season.settled_at is not None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Season is already settled")
    if game.status != GameStatus.SCHEDULED:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "This game is cancelled, so it cannot be moved",
        )
    # Only fields actually sent; null clears a field (the season's venue and
    # time).
    fields = payload.model_dump(exclude_unset=True)
    if "location" in fields:
        game.location = fields["location"]
    if "start_time" in fields:
        game.start_time = fields["start_time"]
    if "end_time" in fields:
        game.end_time = fields["end_time"]

    # The one field here that moves money: the season total moves with the
    # delta, and each member's charge is corrected with an adjustment entry.
    repriced = False
    if "venue_cost_delta" in fields:
        new_delta = fields["venue_cost_delta"]
        if new_delta is None:
            new_delta = Decimal("0")
        if new_delta != game.venue_cost_delta:
            moved = new_delta - game.venue_cost_delta
            if season.total_venue_cost + moved < 0:
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST,
                    "That would make the season's venue cost negative — "
                    "check the amount against the season's total",
                )
            game.venue_cost_delta = new_delta
            season.total_venue_cost += moved
            repriced = True

    new_date = fields.get("date")
    if new_date is not None and new_date != game.date:
        if new_date < _today_in_taiwan():
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, "That date has already been and gone"
            )
        clash = (
            db.query(GameRow)
            .filter(
                GameRow.season_id == game.season_id,
                GameRow.date == new_date,
                GameRow.id != game.id,
            )
            .first()
        )
        if clash is not None:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "This season already has a game on that date",
            )
        game.date = new_date

    if repriced:
        db.flush()
        # Surcharges and deltas larger than the whole venue bill are a
        # refusal, not a 500.
        try:
            _sync_season_fee_ledger(db, season)
        except ValueError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    _audit(
        db,
        current_player,
        "game_updated",
        season.club_id,
        detail=game.date.isoformat(),
    )
    db.commit()
    db.refresh(game)
    return GameOut(id=game.id, date=game.date, status=game.status)
