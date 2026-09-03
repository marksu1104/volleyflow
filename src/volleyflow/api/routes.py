"""API routes."""

from collections import defaultdict
from datetime import UTC, datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from volleyflow.api.conversion import (
    absence_from_row,
    drop_in_from_row,
    game_from_row,
    ledger_entry_from_row,
    player_from_row,
    season_from_rows,
)
from volleyflow.api.dependencies import get_db
from volleyflow.api.schemas import (
    AbsenceCreate,
    AbsenceOut,
    DropInCancelOut,
    DropInCreate,
    DropInOut,
    DropInSummary,
    GameDetailOut,
    GameOut,
    LedgerEntryOut,
    MemberOut,
    MemberSettlementOut,
    PaymentCreate,
    PlayerLedgerOut,
    SeasonCreate,
    SeasonDetailOut,
    SeasonOut,
    SeasonSettleOut,
    SeasonSummaryOut,
    SettlementOut,
)
from volleyflow.db.models import (
    AbsenceRow,
    DropInRow,
    GameRow,
    LedgerEntryRow,
    PlayerRow,
    SeasonMemberRow,
    SeasonRow,
    WaitlistEntryRow,
)
from volleyflow.ledger import EntryType, balance
from volleyflow.pricing import share_per_game
from volleyflow.settlement import MemberSettlement, settle_member

router = APIRouter()


def _now() -> datetime:
    """Server-assigned, UTC, naive — never trust a client-supplied time
    for anything that feeds FIFO ordering (see settlement.py).
    """
    return datetime.now(UTC).replace(tzinfo=None)


def _get_or_create_player(db: Session, name: str) -> PlayerRow:
    """One Player per name, for life — never create a duplicate."""
    player = db.query(PlayerRow).filter(PlayerRow.name == name).first()
    if player is None:
        player = PlayerRow(name=name)
        db.add(player)
        db.flush()  # assigns player.id without ending the transaction
    return player


def _get_player_by_name(db: Session, name: str) -> PlayerRow:
    player = db.query(PlayerRow).filter(PlayerRow.name == name).first()
    if player is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No player named {name!r}")
    return player


def _get_player_or_404(db: Session, player_id: int) -> PlayerRow:
    player = db.get(PlayerRow, player_id)
    if player is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No player with id {player_id}")
    return player


def _get_game_or_404(db: Session, game_id: int) -> GameRow:
    game = db.get(GameRow, game_id)
    if game is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No game with id {game_id}")
    return game


def _require_season_member(db: Session, season_id: int, player_id: int) -> None:
    membership = db.get(
        SeasonMemberRow, {"season_id": season_id, "player_id": player_id}
    )
    if membership is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Player is not a fixed member of this game's season",
        )


def _has_open_slot(db: Session, game: GameRow, season: SeasonRow) -> bool:
    """Expected attendance for this game vs. the season's capacity.

    expected = (fixed members minus this game's absences) + active drop-ins.
    Members who haven't taken leave count as attending by default — see
    CLAUDE.md 2.3, "members are expected by default."
    """
    member_count = (
        db.query(SeasonMemberRow).filter(SeasonMemberRow.season_id == season.id).count()
    )
    absences = db.query(AbsenceRow).filter(AbsenceRow.game_id == game.id).count()
    active_drop_ins = (
        db.query(DropInRow)
        .filter(DropInRow.game_id == game.id, DropInRow.cancelled_at.is_(None))
        .count()
    )
    expected = (member_count - absences) + active_drop_ins
    return expected < season.capacity


def _drop_in_share(db: Session, season_row: SeasonRow) -> Decimal:
    total_games = db.query(GameRow).filter(GameRow.season_id == season_row.id).count()
    member_count = (
        db.query(SeasonMemberRow)
        .filter(SeasonMemberRow.season_id == season_row.id)
        .count()
    )
    return share_per_game(season_row.total_venue_cost, total_games, member_count)


def _record_drop_in_charge(
    db: Session, drop_in: DropInRow, season_row: SeasonRow, *, reverse: bool
) -> None:
    """A confirmed drop-in owes share_per_game for that one game — charged
    the moment they're confirmed (signup or waitlist promotion), reversed
    the moment they cancel. See CLAUDE.md 2.4: "A DropIn pays the per-game
    share, collected by the organizer."
    """
    share = _drop_in_share(db, season_row)
    db.add(
        LedgerEntryRow(
            player_id=drop_in.player_id,
            entry_type=EntryType.DROP_IN_FEE_CHARGED,
            amount=share if reverse else -share,
            recorded_at=_now(),
            season_id=season_row.id,
            note=(
                f"Refund for cancelled drop-in, game {drop_in.game_id}"
                if reverse
                else f"Drop-in fee for game {drop_in.game_id}"
            ),
        )
    )


def _promote_from_waitlist(db: Session, game_id: int) -> int | None:
    """Pull the earliest-queued waitlist entry into a confirmed drop-in,
    charging them the same way a direct signup would be.

    Returns the promoted player's id, or None if nobody was waiting. See
    CLAUDE.md 2.3: "a member records an absence -> the waitlist is offered
    the slot in order."
    """
    entry = (
        db.query(WaitlistEntryRow)
        .filter(WaitlistEntryRow.game_id == game_id)
        .order_by(WaitlistEntryRow.queued_at)
        .first()
    )
    if entry is None:
        return None

    promoted_player_id: int = entry.player_id
    drop_in = DropInRow(
        player_id=promoted_player_id, game_id=game_id, signed_up_at=_now()
    )
    db.add(drop_in)
    db.delete(entry)

    game = db.get(GameRow, game_id)
    assert game is not None
    season_row = db.get(SeasonRow, game.season_id)
    assert season_row is not None
    _record_drop_in_charge(db, drop_in, season_row, reverse=False)

    return promoted_player_id


def _gather_member_settlements(
    db: Session, season_id: int
) -> tuple[SeasonRow, list[MemberSettlement]]:
    """Everything needed to report or record a season's settlement,
    shared by the read-only settlement view and the settle-for-real
    endpoint below so they can never disagree with each other.
    """
    season_row = db.get(SeasonRow, season_id)
    if season_row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No season with id {season_id}")

    game_rows = db.query(GameRow).filter(GameRow.season_id == season_id).all()
    game_ids = [g.id for g in game_rows]
    member_rows = (
        db.query(PlayerRow)
        .join(SeasonMemberRow, SeasonMemberRow.player_id == PlayerRow.id)
        .filter(SeasonMemberRow.season_id == season_id)
        .all()
    )
    absence_rows = db.query(AbsenceRow).filter(AbsenceRow.game_id.in_(game_ids)).all()
    drop_in_rows = db.query(DropInRow).filter(DropInRow.game_id.in_(game_ids)).all()

    player_ids = {p.id for p in member_rows}
    player_ids.update(a.player_id for a in absence_rows)
    player_ids.update(d.player_id for d in drop_in_rows)
    player_rows = db.query(PlayerRow).filter(PlayerRow.id.in_(player_ids)).all()

    players_by_id = {row.id: player_from_row(row) for row in player_rows}
    games_by_id = {row.id: game_from_row(row) for row in game_rows}
    season = season_from_rows(season_row, game_rows, member_rows)
    absences = [absence_from_row(a, players_by_id, games_by_id) for a in absence_rows]
    drop_ins = [drop_in_from_row(d, players_by_id, games_by_id) for d in drop_in_rows]

    settlements = [
        settle_member(players_by_id[m.id], season, absences, drop_ins)
        for m in member_rows
    ]
    return season_row, settlements


def _member_settlement_out(ms: MemberSettlement) -> MemberSettlementOut:
    return MemberSettlementOut(
        player_id=ms.player.id,
        player_name=ms.player.name,
        season_fee=ms.season_fee,
        refund=ms.refund,
        net=ms.net,
    )


@router.get("/seasons", response_model=list[SeasonSummaryOut])
def list_seasons(db: Session = Depends(get_db)) -> list[SeasonSummaryOut]:
    """Enough per season to label it in a picker — dates, not a bare id
    a human has no way to recognize.
    """
    season_rows = db.query(SeasonRow).order_by(SeasonRow.id.desc()).all()
    summaries = []
    for season in season_rows:
        game_dates = [
            g.date
            for g in db.query(GameRow)
            .filter(GameRow.season_id == season.id)
            .order_by(GameRow.date)
            .all()
        ]
        member_count = (
            db.query(SeasonMemberRow)
            .filter(SeasonMemberRow.season_id == season.id)
            .count()
        )
        summaries.append(
            SeasonSummaryOut(
                id=season.id,
                first_game_date=game_dates[0],
                last_game_date=game_dates[-1],
                total_games=len(game_dates),
                member_count=member_count,
                settled=season.settled_at is not None,
            )
        )
    return summaries


@router.post("/seasons", response_model=SeasonOut)
def start_season(payload: SeasonCreate, db: Session = Depends(get_db)) -> SeasonOut:
    season = SeasonRow(
        total_venue_cost=payload.total_venue_cost,
        capacity=payload.capacity,
        minimum_roster=payload.minimum_roster,
        game_start_time=payload.game_start_time,
        game_end_time=payload.game_end_time,
    )
    db.add(season)
    db.flush()

    games = [GameRow(season_id=season.id, date=d) for d in payload.game_dates]
    db.add_all(games)

    member_ids = []
    for name in payload.member_names:
        player = _get_or_create_player(db, name)
        db.add(SeasonMemberRow(season_id=season.id, player_id=player.id))
        member_ids.append(player.id)

    db.commit()
    for game in games:
        db.refresh(game)

    return SeasonOut(
        id=season.id,
        total_venue_cost=season.total_venue_cost,
        capacity=season.capacity,
        minimum_roster=season.minimum_roster,
        game_start_time=season.game_start_time,
        game_end_time=season.game_end_time,
        games=[GameOut(id=g.id, date=g.date, status=g.status) for g in games],
        member_ids=member_ids,
    )


@router.get("/seasons/{season_id}", response_model=SeasonDetailOut)
def get_season(season_id: int, db: Session = Depends(get_db)) -> SeasonDetailOut:
    season_row = db.get(SeasonRow, season_id)
    if season_row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No season with id {season_id}")

    game_rows = db.query(GameRow).filter(GameRow.season_id == season_id).all()
    game_ids = [g.id for g in game_rows]
    member_rows = (
        db.query(PlayerRow)
        .join(SeasonMemberRow, SeasonMemberRow.player_id == PlayerRow.id)
        .filter(SeasonMemberRow.season_id == season_id)
        .all()
    )

    # One query per attendance kind for the whole season, not one per
    # game — looping a query per game (3 * N round trips to Neon for N
    # games) is the classic N+1 problem and was most of why this
    # endpoint felt slow. Group the results by game_id in Python instead.
    absences_by_game: dict[int, list[str]] = defaultdict(list)
    for absence, player in (
        db.query(AbsenceRow, PlayerRow)
        .join(PlayerRow, AbsenceRow.player_id == PlayerRow.id)
        .filter(AbsenceRow.game_id.in_(game_ids))
        .all()
    ):
        absences_by_game[absence.game_id].append(player.name)

    confirmed_by_game: dict[int, list[DropInSummary]] = defaultdict(list)
    for drop_in, player in (
        db.query(DropInRow, PlayerRow)
        .join(PlayerRow, DropInRow.player_id == PlayerRow.id)
        .filter(DropInRow.game_id.in_(game_ids), DropInRow.cancelled_at.is_(None))
        .all()
    ):
        confirmed_by_game[drop_in.game_id].append(
            DropInSummary(id=drop_in.id, player_name=player.name)
        )

    waitlist_by_game: dict[int, list[DropInSummary]] = defaultdict(list)
    for entry, player in (
        db.query(WaitlistEntryRow, PlayerRow)
        .join(PlayerRow, WaitlistEntryRow.player_id == PlayerRow.id)
        .filter(WaitlistEntryRow.game_id.in_(game_ids))
        .order_by(WaitlistEntryRow.queued_at)
        .all()
    ):
        waitlist_by_game[entry.game_id].append(
            DropInSummary(id=entry.id, player_name=player.name)
        )

    games = [
        GameDetailOut(
            id=game.id,
            date=game.date,
            status=game.status,
            absent_player_names=absences_by_game[game.id],
            confirmed_drop_ins=confirmed_by_game[game.id],
            waitlist_entries=waitlist_by_game[game.id],
        )
        for game in game_rows
    ]

    return SeasonDetailOut(
        id=season_row.id,
        total_venue_cost=season_row.total_venue_cost,
        capacity=season_row.capacity,
        minimum_roster=season_row.minimum_roster,
        game_start_time=season_row.game_start_time,
        game_end_time=season_row.game_end_time,
        settled_at=season_row.settled_at,
        members=[MemberOut(id=m.id, name=m.name) for m in member_rows],
        games=games,
    )


@router.post("/absences", response_model=AbsenceOut)
def record_absence(payload: AbsenceCreate, db: Session = Depends(get_db)) -> AbsenceOut:
    player = _get_player_by_name(db, payload.player_name)
    game = _get_game_or_404(db, payload.game_id)
    _require_season_member(db, game.season_id, player.id)

    absence = AbsenceRow(player_id=player.id, game_id=game.id, recorded_at=_now())
    db.add(absence)

    promoted = _promote_from_waitlist(db, game.id)

    db.commit()
    db.refresh(absence)

    return AbsenceOut(
        id=absence.id,
        player_id=absence.player_id,
        game_id=absence.game_id,
        recorded_at=absence.recorded_at,
        promoted_from_waitlist=promoted,
    )


@router.post("/drop-ins", response_model=DropInOut)
def sign_up(payload: DropInCreate, db: Session = Depends(get_db)) -> DropInOut:
    player = _get_or_create_player(db, payload.player_name)
    game = _get_game_or_404(db, payload.game_id)
    season = db.get(SeasonRow, game.season_id)
    assert season is not None  # game.season_id is a foreign key, always valid

    if _has_open_slot(db, game, season):
        drop_in = DropInRow(player_id=player.id, game_id=game.id, signed_up_at=_now())
        db.add(drop_in)
        _record_drop_in_charge(db, drop_in, season, reverse=False)
        db.commit()
        db.refresh(drop_in)
        return DropInOut(
            status="confirmed", id=drop_in.id, player_id=player.id, game_id=game.id
        )

    entry = WaitlistEntryRow(player_id=player.id, game_id=game.id, queued_at=_now())
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return DropInOut(
        status="waitlisted", id=entry.id, player_id=player.id, game_id=game.id
    )


@router.post("/drop-ins/{drop_in_id}/cancel", response_model=DropInCancelOut)
def cancel_drop_in(drop_in_id: int, db: Session = Depends(get_db)) -> DropInCancelOut:
    drop_in = db.get(DropInRow, drop_in_id)
    if drop_in is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, f"No drop-in with id {drop_in_id}"
        )
    if drop_in.cancelled_at is not None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Already cancelled")

    game = db.get(GameRow, drop_in.game_id)
    assert game is not None
    season = db.get(SeasonRow, game.season_id)
    assert season is not None

    drop_in.cancelled_at = _now()
    _record_drop_in_charge(db, drop_in, season, reverse=True)
    promoted = _promote_from_waitlist(db, drop_in.game_id)

    db.commit()
    db.refresh(drop_in)

    return DropInCancelOut(
        id=drop_in.id,
        cancelled_at=drop_in.cancelled_at,
        promoted_from_waitlist=promoted,
    )


@router.get("/seasons/{season_id}/settlement", response_model=SettlementOut)
def view_settlement(season_id: int, db: Session = Depends(get_db)) -> SettlementOut:
    """Read-only preview — what settling right now would charge/refund.
    Doesn't touch the ledger. See POST .../settle to actually record it.
    """
    _, settlements = _gather_member_settlements(db, season_id)
    return SettlementOut(
        season_id=season_id,
        members=[_member_settlement_out(ms) for ms in settlements],
    )


@router.post("/seasons/{season_id}/settle", response_model=SeasonSettleOut)
def settle_season(season_id: int, db: Session = Depends(get_db)) -> SeasonSettleOut:
    """Charges every member's season fee and credits their absence refund
    to the ledger, once. See CLAUDE.md 2.4: settlement happens at season
    end; a season can't be settled twice.
    """
    season_row, settlements = _gather_member_settlements(db, season_id)
    if season_row.settled_at is not None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Season already settled")

    now = _now()
    for ms in settlements:
        db.add(
            LedgerEntryRow(
                player_id=ms.player.id,
                entry_type=EntryType.SEASON_FEE_CHARGED,
                amount=-ms.season_fee,
                recorded_at=now,
                season_id=season_id,
                note=f"Season {season_id} fee",
            )
        )
        if ms.refund > 0:
            db.add(
                LedgerEntryRow(
                    player_id=ms.player.id,
                    entry_type=EntryType.ABSENCE_REFUND,
                    amount=ms.refund,
                    recorded_at=now,
                    season_id=season_id,
                    note=f"Season {season_id} absence refund",
                )
            )

    season_row.settled_at = now
    db.commit()

    return SeasonSettleOut(
        season_id=season_id,
        settled_at=now,
        members=[_member_settlement_out(ms) for ms in settlements],
    )


@router.post("/players/{player_id}/payments", response_model=LedgerEntryOut)
def record_payment(
    player_id: int, payload: PaymentCreate, db: Session = Depends(get_db)
) -> LedgerEntryOut:
    """A manual cash movement the organizer marks by hand — CLAUDE.md 2.4:
    payments and refunds are recorded manually, never via a payment
    gateway. Positive amount: the player paid the organizer. Negative:
    the organizer paid the player.
    """
    _get_player_or_404(db, player_id)

    entry = LedgerEntryRow(
        player_id=player_id,
        entry_type=EntryType.PAYMENT,
        amount=payload.amount,
        recorded_at=_now(),
        season_id=payload.season_id,
        note=payload.note,
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)

    return LedgerEntryOut(
        id=entry.id,
        entry_type=entry.entry_type,
        amount=entry.amount,
        recorded_at=entry.recorded_at,
        season_id=entry.season_id,
        note=entry.note,
    )


@router.get("/players/{player_id}/ledger", response_model=PlayerLedgerOut)
def get_player_ledger(player_id: int, db: Session = Depends(get_db)) -> PlayerLedgerOut:
    """A player's full append-only history and the balance it adds up to.
    Positive balance: the organizer owes the player. Negative: the player
    owes the organizer. Spans every season — see docs/billing-rules.md
    "Ledger" for why nothing ever needs to be explicitly carried over.
    """
    player_row = _get_player_or_404(db, player_id)
    player = player_from_row(player_row)

    entry_rows = (
        db.query(LedgerEntryRow)
        .filter(LedgerEntryRow.player_id == player_id)
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
