"""Payments in, refunds out, and what each person's books say."""

from datetime import date
from decimal import Decimal

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    status,
)
from sqlalchemy import case, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, aliased

from volleyflow.api.conversion import (
    ledger_entry_from_row,
    player_from_row,
)
from volleyflow.api.dependencies import get_db
from volleyflow.api.routes._audit import _audit
from volleyflow.api.routes._money import _counts_up_to, _drop_in_share, _seasons_up_to
from volleyflow.api.routes._people import (
    _get_club_or_404,
    _get_player_or_404,
    _now,
    _require_dealings_with_club,
    _require_organizer,
    _require_self_or_organizer,
    get_current_player,
)
from volleyflow.api.schemas import (
    DropInNightOut,
    LedgerEntryOut,
    PaymentCreate,
    PlayerBalanceOut,
    PlayerLedgerOut,
)
from volleyflow.db.models import (
    DropInRow,
    GameRow,
    LedgerEntryRow,
    PlayerRow,
    SeasonRow,
)
from volleyflow.ledger import EntryType, balance

router = APIRouter()


@router.post(
    "/clubs/{club_id}/players/{player_id}/payments", response_model=LedgerEntryOut
)
def record_payment(
    club_id: int,
    player_id: int,
    payload: PaymentCreate,
    db: Session = Depends(get_db),
    current_player: PlayerRow = Depends(get_current_player),
) -> LedgerEntryOut:
    """A cash payment or refund the organizer marks by hand; there is no
    payment gateway. Positive: the player paid the organizer.
    Negative: the organizer paid the player.
    """
    _get_club_or_404(db, club_id)
    _require_organizer(db, club_id, current_player)
    player = _get_player_or_404(db, player_id)
    _require_dealings_with_club(db, club_id, player_id)
    season_id = payload.season_id
    night: GameRow | None = None
    if payload.game_id is not None:
        game = db.get(GameRow, payload.game_id)
        night_season = db.get(SeasonRow, game.season_id) if game is not None else None
        if night_season is None or night_season.club_id != club_id:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND, f"No game with id {payload.game_id}"
            )
        season_id = night_season.id
        night = game
        # Only from somebody on court that night, never from a queued
        # signup.
        on_court = (
            db.query(DropInRow.id)
            .filter(
                DropInRow.game_id == payload.game_id,
                DropInRow.player_id == player_id,
                DropInRow.playing(),
            )
            .first()
        )
        if on_court is None:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "That player isn't on court for that game",
            )

    # A repeated tap after a lost connection must not record the money
    # twice: a token already seen returns the entry that exists.
    if payload.client_token is not None:
        already = (
            db.query(LedgerEntryRow)
            .filter(LedgerEntryRow.client_token == payload.client_token)
            .first()
        )
        if already is not None:
            return LedgerEntryOut(
                id=already.id,
                entry_type=already.entry_type,
                amount=already.amount,
                recorded_at=already.recorded_at,
                season_id=already.season_id,
                note=already.note,
                game_id=already.game_id,
            )

    entry = LedgerEntryRow(
        player_id=player_id,
        club_id=club_id,
        entry_type=EntryType.PAYMENT,
        amount=payload.amount,
        recorded_at=_now(),
        season_id=season_id,
        game_id=payload.game_id,
        note=payload.note,
        client_token=payload.client_token,
    )
    db.add(entry)
    _audit(
        db,
        current_player,
        "payment",
        club_id,
        subject_name=player.name,
        amount=payload.amount,
        detail=night.date.isoformat() if night is not None else None,
    )
    try:
        db.commit()
    except IntegrityError:
        # Two copies of the same tap both passed the check; the index caught
        # the second, and the payment is recorded either way.
        db.rollback()
        existing = (
            db.query(LedgerEntryRow)
            .filter(LedgerEntryRow.client_token == payload.client_token)
            .first()
        )
        if existing is None:
            raise
        return LedgerEntryOut(
            id=existing.id,
            entry_type=existing.entry_type,
            amount=existing.amount,
            recorded_at=existing.recorded_at,
            season_id=existing.season_id,
            note=existing.note,
            game_id=existing.game_id,
        )
    db.refresh(entry)

    return LedgerEntryOut(
        id=entry.id,
        entry_type=entry.entry_type,
        amount=entry.amount,
        recorded_at=entry.recorded_at,
        season_id=entry.season_id,
        note=entry.note,
        game_id=entry.game_id,
    )


@router.get("/clubs/{club_id}/balances", response_model=list[PlayerBalanceOut])
def list_club_balances(
    club_id: int,
    season_id: int | None = None,
    db: Session = Depends(get_db),
    current_player: PlayerRow = Depends(get_current_player),
) -> list[PlayerBalanceOut]:
    """Every player's balance in this club, summed by the database in one
    query. Organizer only; a member reads their own through the
    per-player ledger.
    """
    _get_club_or_404(db, club_id)
    _require_organizer(db, club_id, current_player)

    in_season = LedgerEntryRow.season_id == season_id
    up_to = _counts_up_to(_season_ids_up_to(db, club_id, season_id))
    rows = (
        db.query(
            LedgerEntryRow.player_id,
            func.sum(LedgerEntryRow.amount),
            func.sum(case((in_season, LedgerEntryRow.amount), else_=0)),
            func.sum(case((up_to, LedgerEntryRow.amount), else_=0)),
            func.sum(
                case(
                    (
                        in_season
                        & (LedgerEntryRow.entry_type == EntryType.SEASON_FEE_CHARGED),
                        LedgerEntryRow.amount,
                    ),
                    else_=0,
                )
            ),
        )
        .filter(LedgerEntryRow.club_id == club_id)
        .group_by(LedgerEntryRow.player_id)
        .all()
    )
    brought_by = _who_brought(db, club_id)
    nights = _drop_in_nights(db, club_id, season_id)
    return [
        PlayerBalanceOut(
            player_id=player_id,
            balance=balance_total,
            season_total=season_total,
            season_fee_charged=season_fee,
            through_season=through,
            nights=nights.get(player_id, []),
            brought_by=brought_by.get(player_id),
        )
        for player_id, balance_total, season_total, through, season_fee in rows
    ]


def _drop_in_nights(
    db: Session, club_id: int, season_id: int | None
) -> dict[int, list[DropInNightOut]]:
    """Player id -> each night of the season they play as a drop-in, with
    that night's fee and what was paid for it.
    """
    season = db.get(SeasonRow, season_id) if season_id is not None else None
    if season is None or season.club_id != club_id:
        return {}
    paid: dict[tuple[int, int], Decimal] = {
        (player_id, game_id): amount
        for player_id, game_id, amount in db.query(
            LedgerEntryRow.player_id,
            LedgerEntryRow.game_id,
            func.sum(LedgerEntryRow.amount),
        )
        .filter(
            LedgerEntryRow.club_id == club_id,
            LedgerEntryRow.entry_type == EntryType.PAYMENT,
            LedgerEntryRow.game_id.is_not(None),
        )
        .group_by(LedgerEntryRow.player_id, LedgerEntryRow.game_id)
    }
    nights: dict[int, list[DropInNightOut]] = {}
    for drop_in in (
        db.query(DropInRow)
        .join(GameRow, GameRow.id == DropInRow.game_id)
        .filter(GameRow.season_id == season.id, DropInRow.playing())
        .order_by(GameRow.date)
    ):
        charged = drop_in.charged_amount
        if charged is None:
            charged = _drop_in_share(db, season, drop_in.game_id)
        nights.setdefault(drop_in.player_id, []).append(
            DropInNightOut(
                game_id=drop_in.game_id,
                charged=charged,
                paid=paid.get((drop_in.player_id, drop_in.game_id), Decimal(0)),
            )
        )
    return nights


def _season_ids_up_to(
    db: Session, club_id: int, season_id: int | None
) -> list[int] | None:
    """`_seasons_up_to` for a season named in the query string, or None
    (every season) when none is named or it is another club's.
    """
    if season_id is None:
        return None
    season = db.get(SeasonRow, season_id)
    if season is None or season.club_id != club_id:
        return None
    return _seasons_up_to(db, season)


def _who_brought(db: Session, club_id: int) -> dict[int, str]:
    """Guest player id -> who signed them up. A guest's fee is on their
    own ledger, but the member who brought them is who hands over the
    cash. The roster shows the same thing
    (DropInDetailOut.brought_by_name).
    """
    Bringer = aliased(PlayerRow)
    rows = (
        db.query(DropInRow.player_id, Bringer.name)
        .join(Bringer, Bringer.id == DropInRow.brought_by_player_id)
        .join(GameRow, GameRow.id == DropInRow.game_id)
        .join(SeasonRow, SeasonRow.id == GameRow.season_id)
        .filter(SeasonRow.club_id == club_id, DropInRow.playing())
        .distinct()
        .all()
    )
    names: dict[int, list[str]] = {}
    for player_id, bringer_name in rows:
        names.setdefault(player_id, []).append(bringer_name)
    # Two names are enough to know whom to ask.
    return {
        player_id: "、".join(sorted(who)[:2]) + ("等" if len(who) > 2 else "")
        for player_id, who in names.items()
    }


@router.get(
    "/clubs/{club_id}/players/{player_id}/ledger", response_model=PlayerLedgerOut
)
def get_player_ledger(
    club_id: int,
    player_id: int,
    db: Session = Depends(get_db),
    current_player: PlayerRow = Depends(get_current_player),
) -> PlayerLedgerOut:
    """A player's full history in this club and the balance it adds up to
    (positive: the organizer owes them). Spans every season of the
    club; a player in two clubs has two separate balances. The player
    or the club's organizer only.
    """
    _get_club_or_404(db, club_id)
    _require_self_or_organizer(db, club_id, current_player, player_id)
    player_row = _get_player_or_404(db, player_id)
    player = player_from_row(player_row)

    entry_rows = (
        db.query(LedgerEntryRow)
        .filter(
            LedgerEntryRow.player_id == player_id, LedgerEntryRow.club_id == club_id
        )
        .order_by(LedgerEntryRow.recorded_at)
        .all()
    )
    entries = [ledger_entry_from_row(row, player) for row in entry_rows]
    season_ids = {row.season_id for row in entry_rows if row.season_id is not None}
    season_starts: dict[int, date] = {
        season_id: start
        for season_id, start in db.query(GameRow.season_id, func.min(GameRow.date))
        .filter(GameRow.season_id.in_(season_ids))
        .group_by(GameRow.season_id)
        .all()
    }

    return PlayerLedgerOut(
        player_id=player.id,
        player_name=player.name,
        balance=balance(entries),
        season_starts=season_starts,
        entries=[
            LedgerEntryOut(
                id=row.id,
                entry_type=row.entry_type,
                amount=row.amount,
                recorded_at=row.recorded_at,
                season_id=row.season_id,
                note=row.note,
                reverses_entry_id=row.reverses_entry_id,
            )
            for row in entry_rows
        ],
    )


@router.post("/ledger-entries/{entry_id}/reverse", response_model=LedgerEntryOut)
def reverse_payment(
    entry_id: int,
    db: Session = Depends(get_db),
    current_player: PlayerRow = Depends(get_current_player),
) -> LedgerEntryOut:
    """Undoes a payment or refund marked by mistake with an opposite
    entry; the original stays.
    """
    entry = db.get(LedgerEntryRow, entry_id)
    if entry is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such ledger entry")
    _require_organizer(db, entry.club_id, current_player)
    if entry.entry_type is not EntryType.PAYMENT or entry.reverses_entry_id is not None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "Only a payment or refund can be undone"
        )
    already = (
        db.query(LedgerEntryRow)
        .filter(LedgerEntryRow.reverses_entry_id == entry.id)
        .first()
    )
    if already is not None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "That payment has already been undone"
        )

    undo = LedgerEntryRow(
        player_id=entry.player_id,
        club_id=entry.club_id,
        entry_type=EntryType.PAYMENT,
        amount=-entry.amount,
        recorded_at=_now(),
        season_id=entry.season_id,
        game_id=entry.game_id,
        reverses_entry_id=entry.id,
    )
    db.add(undo)
    _audit(
        db,
        current_player,
        "payment_undone",
        entry.club_id,
        subject_id=entry.player_id,
        amount=entry.amount,
    )
    try:
        db.commit()
    except IntegrityError:
        # Two taps on 復原 at once: the unique index caught the second.
        db.rollback()
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "That payment has already been undone"
        ) from None
    db.refresh(undo)
    return LedgerEntryOut(
        id=undo.id,
        entry_type=undo.entry_type,
        amount=undo.amount,
        recorded_at=undo.recorded_at,
        season_id=undo.season_id,
        note=undo.note,
        reverses_entry_id=undo.reverses_entry_id,
        game_id=undo.game_id,
    )
