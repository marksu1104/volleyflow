"""API routes."""

from collections import defaultdict
from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal
from typing import cast

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
    AbsenceCancelOut,
    AbsenceCreate,
    AbsenceDetailOut,
    AbsenceOut,
    DropInCancelOut,
    DropInCreate,
    DropInDetailOut,
    DropInOut,
    DropInSummary,
    GameDetailOut,
    GameOut,
    Gender,
    GenderUpdate,
    LedgerEntryOut,
    MemberAdd,
    MemberOut,
    MemberSettlementOut,
    PaymentCreate,
    PlayerLedgerOut,
    SeasonCreate,
    SeasonDetailOut,
    SeasonOut,
    SeasonSettleOut,
    SeasonSummaryOut,
    SeasonUpdate,
    SettlementOut,
    SubstituteCreate,
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
from volleyflow.settlement import MemberSettlement, covered_absences, settle_member

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
    """Locks the game row for the rest of this transaction.

    Every caller of this reads the game's current roster to decide
    something — is there an open slot, who's next on the waitlist — then
    writes based on that read. Without the lock, two concurrent requests
    for the same game (e.g. a burst of drop-ins racing for a slot that
    just opened) can each read "one slot open" before either commits,
    and both get confirmed past capacity. SELECT ... FOR UPDATE makes the
    second request wait for the first to commit, so it sees the first
    request's write before making its own decision. SQLite ignores this
    (no row locking support) — only the `postgres`-marked tests actually
    exercise it.
    """
    game = db.query(GameRow).filter(GameRow.id == game_id).with_for_update().first()
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


def _within_change_deadline(game: GameRow, season: SeasonRow) -> bool:
    """Whether absence/signup changes (and cancelling either) are still
    allowed for this game. None means no deadline — CLAUDE.md 2.3's
    stated default; otherwise a change must land at least this many
    days before the game.
    """
    if season.change_deadline_days is None:
        return True
    return _today_in_taiwan() + timedelta(days=season.change_deadline_days) <= game.date


def _require_within_change_deadline(game: GameRow, season: SeasonRow) -> None:
    if not _within_change_deadline(game, season):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Past this season's change deadline for this game",
        )


def _is_absence_covered(db: Session, absence_row: AbsenceRow) -> bool:
    """Whether a drop-in — an explicit substitute or a FIFO match — is
    currently covering this absence, per the same rule settlement.py
    uses for refunds. Cancelling an absence out from under someone who
    already committed to cover it needs the organizer, not a silent
    auto-fix, so callers use this to block that case.
    """
    game_row = db.get(GameRow, absence_row.game_id)
    assert game_row is not None
    game = game_from_row(game_row)

    absence_rows = db.query(AbsenceRow).filter(AbsenceRow.game_id == game_row.id).all()
    drop_in_rows = db.query(DropInRow).filter(DropInRow.game_id == game_row.id).all()
    player_ids = {a.player_id for a in absence_rows} | {
        d.player_id for d in drop_in_rows
    }
    players_by_id = {
        p.id: player_from_row(p)
        for p in db.query(PlayerRow).filter(PlayerRow.id.in_(player_ids)).all()
    }
    games_by_id = {game_row.id: game}
    absences_by_id = {
        row.id: absence_from_row(row, players_by_id, games_by_id)
        for row in absence_rows
    }
    drop_ins = [
        drop_in_from_row(row, players_by_id, games_by_id, absences_by_id)
        for row in drop_in_rows
    ]
    covered = covered_absences(game, list(absences_by_id.values()), drop_ins)
    return absences_by_id[absence_row.id] in covered


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
    absences_by_id = {
        row.id: absence_from_row(row, players_by_id, games_by_id)
        for row in absence_rows
    }
    absences = list(absences_by_id.values())
    drop_ins = [
        drop_in_from_row(d, players_by_id, games_by_id, absences_by_id)
        for d in drop_in_rows
    ]

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
        location=payload.location,
        change_deadline_days=payload.change_deadline_days,
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
        location=season.location,
        change_deadline_days=season.change_deadline_days,
        games=[GameOut(id=g.id, date=g.date, status=g.status) for g in games],
        member_ids=member_ids,
    )


def _season_out(db: Session, season: SeasonRow) -> SeasonOut:
    games = db.query(GameRow).filter(GameRow.season_id == season.id).all()
    member_ids = [
        row.player_id
        for row in db.query(SeasonMemberRow)
        .filter(SeasonMemberRow.season_id == season.id)
        .all()
    ]
    return SeasonOut(
        id=season.id,
        total_venue_cost=season.total_venue_cost,
        capacity=season.capacity,
        minimum_roster=season.minimum_roster,
        game_start_time=season.game_start_time,
        game_end_time=season.game_end_time,
        location=season.location,
        change_deadline_days=season.change_deadline_days,
        games=[GameOut(id=g.id, date=g.date, status=g.status) for g in games],
        member_ids=member_ids,
    )


@router.patch("/seasons/{season_id}", response_model=SeasonOut)
def update_season(
    season_id: int, payload: SeasonUpdate, db: Session = Depends(get_db)
) -> SeasonOut:
    """A partial update — only fields the client actually sent are
    touched (see SeasonUpdate). Changing `total_venue_cost` changes
    every member's per-game share for the whole season (past games
    included, since there's one season-wide split, not a per-period
    one) — the frontend warns about this before calling in; once
    settled, the ledger already reflects the old cost, so it's locked.
    """
    season = db.get(SeasonRow, season_id)
    if season is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No season with id {season_id}")

    updates = payload.model_dump(exclude_unset=True)
    if "total_venue_cost" in updates and season.settled_at is not None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Season is already settled — venue cost can't change now",
        )

    for field, value in updates.items():
        setattr(season, field, value)

    db.commit()
    db.refresh(season)
    return _season_out(db, season)


@router.post("/seasons/{season_id}/members", response_model=MemberOut)
def add_member(
    season_id: int, payload: MemberAdd, db: Session = Depends(get_db)
) -> MemberOut:
    """Adding a member changes everyone's per-game share for the whole
    season, same reasoning as changing the venue cost — the frontend
    warns before calling this. Blocked once settled: the ledger already
    reflects the season fee computed from the roster at that time.
    """
    season = db.get(SeasonRow, season_id)
    if season is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No season with id {season_id}")
    if season.settled_at is not None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Season is already settled")

    player = _get_or_create_player(db, payload.player_name)
    existing = db.get(SeasonMemberRow, {"season_id": season_id, "player_id": player.id})
    if existing is not None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "Already a member of this season"
        )

    db.add(SeasonMemberRow(season_id=season_id, player_id=player.id))
    db.commit()
    db.refresh(player)
    return MemberOut(id=player.id, name=player.name, gender=_gender(player.gender))


@router.delete("/seasons/{season_id}/members/{player_id}", status_code=204)
def remove_member(
    season_id: int, player_id: int, db: Session = Depends(get_db)
) -> None:
    """Same retroactive-share caveat as adding one. Doesn't touch this
    player's past absence/drop-in rows for this season — they simply
    stop counting toward anyone's settlement once removed, since that
    only ever iterates the current member list.
    """
    season = db.get(SeasonRow, season_id)
    if season is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No season with id {season_id}")
    if season.settled_at is not None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Season is already settled")

    membership = db.get(
        SeasonMemberRow, {"season_id": season_id, "player_id": player_id}
    )
    if membership is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not a member of this season")

    db.delete(membership)
    db.commit()


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
    # Absences and drop-ins are each ordered earliest-first per game so
    # they can be paired off FIFO below — same rule, applied here purely
    # for display, as settlement.covered_absences applies for refunds.
    # An explicit substitute (covers_absence_id) always pairs with that
    # absence regardless of order, exactly as covered_absences does.
    absences_by_game: dict[int, list[tuple[int, str]]] = defaultdict(list)
    for absence, player in (
        db.query(AbsenceRow, PlayerRow)
        .join(PlayerRow, AbsenceRow.player_id == PlayerRow.id)
        .filter(AbsenceRow.game_id.in_(game_ids), AbsenceRow.cancelled_at.is_(None))
        .order_by(AbsenceRow.recorded_at)
        .all()
    ):
        absences_by_game[absence.game_id].append((absence.id, player.name))

    drop_ins_by_game: dict[int, list[tuple[int, str, Gender | None, int | None]]] = (
        defaultdict(list)
    )
    for drop_in, player in (
        db.query(DropInRow, PlayerRow)
        .join(PlayerRow, DropInRow.player_id == PlayerRow.id)
        .filter(DropInRow.game_id.in_(game_ids), DropInRow.cancelled_at.is_(None))
        .order_by(DropInRow.signed_up_at)
        .all()
    ):
        drop_ins_by_game[drop_in.game_id].append(
            (drop_in.id, player.name, _gender(player.gender), drop_in.covers_absence_id)
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
            DropInSummary(
                id=entry.id, player_name=player.name, gender=_gender(player.gender)
            )
        )

    games = []
    for game in game_rows:
        # (absence_id, name) pairs, FIFO order
        absences_list = absences_by_game[game.id]
        # (drop_in_id, name, gender, covers_absence_id) tuples
        drop_ins = drop_ins_by_game[game.id]

        # Explicit substitutes claim their absence first; the remaining
        # (unclaimed) absences pair FIFO with the remaining drop-ins —
        # same two-pass rule as settlement.covered_absences, just
        # producing a name-to-name display instead of a refund count.
        absence_name_by_id = dict(absences_list)
        covered_by_name: dict[str, str] = {}
        covering_name: dict[int, str] = {}
        claimed_absence_ids: set[int] = set()
        for drop_in_id, name, _drop_in_gender, covers_absence_id in drop_ins:
            if covers_absence_id in absence_name_by_id:
                absence_name = absence_name_by_id[covers_absence_id]
                covered_by_name[absence_name] = name
                covering_name[drop_in_id] = absence_name
                claimed_absence_ids.add(covers_absence_id)

        fifo_absences = [
            (aid, name) for aid, name in absences_list if aid not in claimed_absence_ids
        ]
        fifo_drop_ins = [
            (drop_in_id, name)
            for drop_in_id, name, _drop_in_gender, covers_absence_id in drop_ins
            if covers_absence_id is None
        ]
        for i, (_aid, absence_name) in enumerate(fifo_absences):
            if i < len(fifo_drop_ins):
                covered_by_name[absence_name] = fifo_drop_ins[i][1]
        for i, (drop_in_id, _name) in enumerate(fifo_drop_ins):
            if i < len(fifo_absences):
                covering_name[drop_in_id] = fifo_absences[i][1]

        games.append(
            GameDetailOut(
                id=game.id,
                date=game.date,
                status=game.status,
                locked=not _within_change_deadline(game, season_row),
                absences=[
                    AbsenceDetailOut(
                        id=aid, player_name=name, covered_by=covered_by_name.get(name)
                    )
                    for aid, name in absences_list
                ],
                confirmed_drop_ins=[
                    DropInDetailOut(
                        id=drop_in_id,
                        player_name=name,
                        gender=gender,
                        covering=covering_name.get(drop_in_id),
                    )
                    for drop_in_id, name, gender, _covers in drop_ins
                ],
                waitlist_entries=waitlist_by_game[game.id],
            )
        )

    return SeasonDetailOut(
        id=season_row.id,
        total_venue_cost=season_row.total_venue_cost,
        capacity=season_row.capacity,
        minimum_roster=season_row.minimum_roster,
        game_start_time=season_row.game_start_time,
        game_end_time=season_row.game_end_time,
        location=season_row.location,
        change_deadline_days=season_row.change_deadline_days,
        share_per_game=share_per_game(
            season_row.total_venue_cost, len(game_rows), len(member_rows)
        ),
        settled_at=season_row.settled_at,
        members=[
            MemberOut(id=m.id, name=m.name, gender=_gender(m.gender))
            for m in member_rows
        ],
        games=games,
    )


@router.post("/absences", response_model=AbsenceOut)
def record_absence(payload: AbsenceCreate, db: Session = Depends(get_db)) -> AbsenceOut:
    player = _get_player_by_name(db, payload.player_name)
    game = _get_game_or_404(db, payload.game_id)
    _require_season_member(db, game.season_id, player.id)
    season = db.get(SeasonRow, game.season_id)
    assert season is not None
    _require_within_change_deadline(game, season)

    existing = (
        db.query(AbsenceRow)
        .filter(
            AbsenceRow.player_id == player.id,
            AbsenceRow.game_id == game.id,
            AbsenceRow.cancelled_at.is_(None),
        )
        .first()
    )
    if existing is not None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "This player already has an absence recorded for this game",
        )

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


@router.post("/absences/{absence_id}/cancel", response_model=AbsenceCancelOut)
def cancel_absence(absence_id: int, db: Session = Depends(get_db)) -> AbsenceCancelOut:
    """The member is attending after all. Only allowed while nothing is
    covering this absence yet (see _is_absence_covered) — undoing it out
    from under someone who already committed to cover needs the
    organizer, not a silent auto-fix.
    """
    absence = db.get(AbsenceRow, absence_id)
    if absence is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, f"No absence with id {absence_id}"
        )
    if absence.cancelled_at is not None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Already cancelled")

    game = _get_game_or_404(db, absence.game_id)
    season = db.get(SeasonRow, game.season_id)
    assert season is not None
    _require_within_change_deadline(game, season)

    if _is_absence_covered(db, absence):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Someone is already covering this absence — ask the organizer",
        )

    absence.cancelled_at = _now()
    db.commit()
    db.refresh(absence)

    return AbsenceCancelOut(id=absence.id, cancelled_at=absence.cancelled_at)


@router.put("/absences/{absence_id}/substitute", response_model=DropInOut)
def set_substitute(
    absence_id: int, payload: SubstituteCreate, db: Session = Depends(get_db)
) -> DropInOut:
    """A member (or the organizer) personally arranging — or later
    changing — who covers a specific absence, a "代打". Always
    confirmed, never queued: filling your own slot with someone you
    picked isn't competing with the waitlist for open capacity. See
    attendance.DropIn.covers.

    No change-deadline check here, on purpose: swapping who's covering
    doesn't create the understaffed-at-the-last-minute risk the
    deadline protects against, since a body still fills the slot
    either way. Idempotent by design — call this again with a new name
    to replace whoever's currently covering; the previous substitute's
    charge is refunded first. Actually removing coverage (leaving the
    absence uncovered) still goes through the ordinary
    /drop-ins/{id}/cancel, which does check the deadline.
    """
    absence = db.get(AbsenceRow, absence_id)
    if absence is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, f"No absence with id {absence_id}"
        )
    if absence.cancelled_at is not None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "This absence was cancelled")

    game = _get_game_or_404(db, absence.game_id)
    season = db.get(SeasonRow, game.season_id)
    assert season is not None

    existing = (
        db.query(DropInRow)
        .filter(
            DropInRow.covers_absence_id == absence_id,
            DropInRow.cancelled_at.is_(None),
        )
        .first()
    )
    if existing is not None:
        existing.cancelled_at = _now()
        _record_drop_in_charge(db, existing, season, reverse=True)
        # Flush now, before the new DropInRow below is added: SQLAlchemy's
        # unit of work orders all pending INSERTs before UPDATEs regardless
        # of the order they were issued in, so without this the new row
        # (e.g. re-assigning the same person) would be inserted while the
        # old one is still active, tripping the active-substitute unique
        # index.
        db.flush()

    player = _get_or_create_player(db, payload.player_name)
    if player.gender is None and payload.gender is not None:
        player.gender = payload.gender

    # Any active drop-in still on file for this player+game at this point
    # can't be the one that covered this absence — that one was just
    # cancelled (and flushed) above, if it existed. So a match here is
    # necessarily a different signup.
    other_drop_in = (
        db.query(DropInRow)
        .filter(
            DropInRow.player_id == player.id,
            DropInRow.game_id == game.id,
            DropInRow.cancelled_at.is_(None),
        )
        .first()
    )
    if other_drop_in is not None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "That player is already signed up for this game",
        )

    drop_in = DropInRow(
        player_id=player.id,
        game_id=game.id,
        signed_up_at=_now(),
        covers_absence_id=absence_id,
    )
    db.add(drop_in)
    _record_drop_in_charge(db, drop_in, season, reverse=False)
    db.commit()
    db.refresh(drop_in)

    return DropInOut(
        status="confirmed", id=drop_in.id, player_id=player.id, game_id=game.id
    )


@router.post("/drop-ins", response_model=DropInOut)
def sign_up(payload: DropInCreate, db: Session = Depends(get_db)) -> DropInOut:
    player = _get_or_create_player(db, payload.player_name)
    game = _get_game_or_404(db, payload.game_id)
    season = db.get(SeasonRow, game.season_id)
    assert season is not None  # game.season_id is a foreign key, always valid
    _require_within_change_deadline(game, season)

    already_signed_up = (
        db.query(DropInRow)
        .filter(
            DropInRow.player_id == player.id,
            DropInRow.game_id == game.id,
            DropInRow.cancelled_at.is_(None),
        )
        .first()
    )
    if already_signed_up is not None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "This player is already signed up for this game",
        )
    already_waitlisted = (
        db.query(WaitlistEntryRow)
        .filter(
            WaitlistEntryRow.player_id == player.id,
            WaitlistEntryRow.game_id == game.id,
        )
        .first()
    )
    if already_waitlisted is not None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "This player is already on the waitlist for this game",
        )

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

    game = _get_game_or_404(db, drop_in.game_id)
    season = db.get(SeasonRow, game.season_id)
    assert season is not None
    _require_within_change_deadline(game, season)

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


@router.put("/players/{player_id}/gender", response_model=MemberOut)
def set_player_gender(
    player_id: int, payload: GenderUpdate, db: Session = Depends(get_db)
) -> MemberOut:
    """Self-reported by the player — never billing-relevant, only shown
    on the roster so a game's expected male/female split is visible.
    """
    player = _get_player_or_404(db, player_id)
    player.gender = payload.gender
    db.commit()
    db.refresh(player)
    return MemberOut(id=player.id, name=player.name, gender=_gender(player.gender))


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
