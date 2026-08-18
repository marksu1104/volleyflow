"""API routes."""

from collections import defaultdict
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from volleyflow.api.conversion import (
    absence_from_row,
    drop_in_from_row,
    game_from_row,
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
    MemberOut,
    MemberSettlementOut,
    SeasonCreate,
    SeasonDetailOut,
    SeasonOut,
    SettlementOut,
)
from volleyflow.db.models import (
    AbsenceRow,
    DropInRow,
    GameRow,
    PlayerRow,
    SeasonMemberRow,
    SeasonRow,
    WaitlistEntryRow,
)
from volleyflow.settlement import settle_member

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


def _promote_from_waitlist(db: Session, game_id: int) -> int | None:
    """Pull the earliest-queued waitlist entry into a confirmed drop-in.

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
    db.add(
        DropInRow(player_id=promoted_player_id, game_id=game_id, signed_up_at=_now())
    )
    db.delete(entry)
    return promoted_player_id


@router.post("/seasons", response_model=SeasonOut)
def start_season(payload: SeasonCreate, db: Session = Depends(get_db)) -> SeasonOut:
    season = SeasonRow(
        total_venue_cost=payload.total_venue_cost, capacity=payload.capacity
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

    drop_in.cancelled_at = _now()
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

    # Every player referenced anywhere in this season's data — members,
    # plus whoever recorded an absence or signed up as a drop-in.
    player_ids = {p.id for p in member_rows}
    player_ids.update(a.player_id for a in absence_rows)
    player_ids.update(d.player_id for d in drop_in_rows)
    player_rows = db.query(PlayerRow).filter(PlayerRow.id.in_(player_ids)).all()

    players_by_id = {row.id: player_from_row(row) for row in player_rows}
    games_by_id = {row.id: game_from_row(row) for row in game_rows}

    season = season_from_rows(season_row, game_rows, member_rows)
    absences = [absence_from_row(a, players_by_id, games_by_id) for a in absence_rows]
    drop_ins = [drop_in_from_row(d, players_by_id, games_by_id) for d in drop_in_rows]

    members = [
        settle_member(players_by_id[row.id], season, absences, drop_ins)
        for row in member_rows
    ]

    return SettlementOut(
        season_id=season.id,
        members=[
            MemberSettlementOut(
                player_id=ms.player.id,
                player_name=ms.player.name,
                season_fee=ms.season_fee,
                refund=ms.refund,
                net=ms.net,
            )
            for ms in members
        ],
    )
