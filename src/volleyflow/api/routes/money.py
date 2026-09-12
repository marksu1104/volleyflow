"""Payments in, refunds out, and what each person's books say."""

from fastapi import (
    APIRouter,
    Depends,
)
from sqlalchemy import case, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, aliased

from volleyflow.api.conversion import (
    ledger_entry_from_row,
    player_from_row,
)
from volleyflow.api.dependencies import get_db
from volleyflow.api.routes._people import (
    _get_club_or_404,
    _get_player_or_404,
    _now,
    _require_club_member,
    _require_organizer,
    _require_self_or_organizer,
    get_current_player,
)
from volleyflow.api.schemas import (
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
    """A manual cash movement the organizer marks by hand — CLAUDE.md 2.4:
    payments and refunds are recorded manually, never via a payment
    gateway. Positive amount: the player paid the organizer. Negative:
    the organizer paid the player.
    """
    _get_club_or_404(db, club_id)
    _require_organizer(db, club_id, current_player)
    _get_player_or_404(db, player_id)
    _require_club_member(db, club_id, player_id)

    # Recording money is the one thing that must not happen twice because
    # a phone lost signal mid-request and the tap was repeated. If this
    # token has been seen, the payment is already recorded: hand back the
    # entry that exists rather than writing another.
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
            )

    entry = LedgerEntryRow(
        player_id=player_id,
        club_id=club_id,
        entry_type=EntryType.PAYMENT,
        amount=payload.amount,
        recorded_at=_now(),
        season_id=payload.season_id,
        note=payload.note,
        client_token=payload.client_token,
    )
    db.add(entry)
    try:
        db.commit()
    except IntegrityError:
        # Two copies of the same tap arrived close enough together that
        # both passed the check above; the index caught the loser. The
        # payment is recorded either way, which is all the caller needs.
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
        )
    db.refresh(entry)

    return LedgerEntryOut(
        id=entry.id,
        entry_type=entry.entry_type,
        amount=entry.amount,
        recorded_at=entry.recorded_at,
        season_id=entry.season_id,
        note=entry.note,
    )


@router.get("/clubs/{club_id}/balances", response_model=list[PlayerBalanceOut])
def list_club_balances(
    club_id: int,
    season_id: int | None = None,
    db: Session = Depends(get_db),
    current_player: PlayerRow = Depends(get_current_player),
) -> list[PlayerBalanceOut]:
    """Every player's balance in this club, in one query.

    The ledger screen needs a number per person, and fetching each
    person's full entry history to add it up in the browser meant one
    request per member — around forty on a page load for a normal club,
    against a six-connection browser limit. The sums are trivial for the
    database to do together, so it does.

    Organizer-only: this is the whole club's books at a glance. A member
    reading their own balance still goes through the per-player ledger
    endpoint, which shows the entries behind the number.
    """
    _get_club_or_404(db, club_id)
    _require_organizer(db, club_id, current_player)

    in_season = LedgerEntryRow.season_id == season_id
    rows = (
        db.query(
            LedgerEntryRow.player_id,
            func.sum(LedgerEntryRow.amount),
            func.sum(case((in_season, LedgerEntryRow.amount), else_=0)),
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
    return [
        PlayerBalanceOut(
            player_id=player_id,
            balance=balance_total,
            season_total=season_total,
            season_fee_charged=season_fee,
            brought_by=brought_by.get(player_id),
        )
        for player_id, balance_total, season_total, season_fee in rows
    ]


def _who_brought(db: Session, club_id: int) -> dict[int, str]:
    """Guest player id -> the name(s) of whoever signed them up.

    Only ever shown on the money screen. A guest's fee is charged to
    their own ledger, but they have no account and pay nothing — the
    member who brought them hands over the cash — so "who do I collect
    this from" is otherwise unanswerable, and on a night when three
    members each bring somebody it is guesswork. Deliberately absent
    from the roster, where it would be noise.
    """
    Bringer = aliased(PlayerRow)
    rows = (
        db.query(DropInRow.player_id, Bringer.name)
        .join(Bringer, Bringer.id == DropInRow.brought_by_player_id)
        .join(GameRow, GameRow.id == DropInRow.game_id)
        .join(SeasonRow, SeasonRow.id == GameRow.season_id)
        .filter(SeasonRow.club_id == club_id, DropInRow.cancelled_at.is_(None))
        .distinct()
        .all()
    )
    names: dict[int, list[str]] = {}
    for player_id, bringer_name in rows:
        names.setdefault(player_id, []).append(bringer_name)
    # Two names is already enough to go and ask; beyond that the list
    # stops fitting on a phone and stops being the point.
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
    """A player's full append-only history within this club and the
    balance it adds up to. Positive balance: the organizer owes the
    player. Negative: the player owes the organizer. Spans every season
    *in this club* — see docs/billing-rules.md "Ledger" for why nothing
    ever needs to be explicitly carried over. A player active in two
    clubs has two separate balances, never combined. Self-or-organizer:
    money is sensitive enough that only the player themselves or their
    club's organizer should be able to read it.
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

    return PlayerLedgerOut(
        player_id=player.id,
        player_name=player.name,
        balance=balance(entries),
        entries=[
            LedgerEntryOut(
                id=row.id,
                entry_type=row.entry_type,
                amount=row.amount,
                recorded_at=row.recorded_at,
                season_id=row.season_id,
                note=row.note,
            )
            for row in entry_rows
        ],
    )
