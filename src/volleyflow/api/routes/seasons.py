"""Seasons: creating one, reading it, changing its terms, and deleting it."""

from collections import defaultdict
from dataclasses import replace
from decimal import Decimal

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    status,
)
from sqlalchemy import and_
from sqlalchemy.orm import Session, aliased

from volleyflow.api.conversion import (
    season_from_rows,
)
from volleyflow.api.dependencies import get_db
from volleyflow.api.routes._attendance import (
    _delete_season_rows,
    _expected_on_court,
    _offer_freed_slots_to_the_queue,
    _within_change_deadline,
)
from volleyflow.api.routes._audit import _audit, _season_span
from volleyflow.api.routes._money import (
    _previous_season,
    _sync_season_fee_ledger,
)
from volleyflow.api.routes._people import (
    _gender,
    _get_club_or_404,
    _get_or_create_player,
    _require_club_access,
    _require_organizer,
    _runs_club,
    _today_in_taiwan,
    get_current_player,
)
from volleyflow.api.schemas import (
    AbsenceDetailOut,
    AirConditionedDatesUpdate,
    AirConditionedPreviewGame,
    AirConditionedPreviewOut,
    DropInDetailOut,
    DropInSummary,
    GameDetailOut,
    GameOut,
    Gender,
    MemberOut,
    SeasonCreate,
    SeasonDetailOut,
    SeasonOut,
    SeasonSummaryOut,
    SeasonUpdate,
)
from volleyflow.db.models import (
    AbsenceRow,
    DropInRow,
    GameRow,
    LedgerEntryRow,
    PlayerRow,
    SeasonMemberRow,
    SeasonRow,
)
from volleyflow.ledger import EntryType
from volleyflow.pricing import share_per_game
from volleyflow.schedule import GameStatus, Season
from volleyflow.settlement import (
    season_shares,
)

router = APIRouter()


@router.delete("/seasons/{season_id}", status_code=204)
def delete_season(
    season_id: int,
    db: Session = Depends(get_db),
    current_player: PlayerRow = Depends(get_current_player),
) -> None:
    """Deletes a season with its games, attendance and ledger entries;
    meant for a season created by mistake.

    Refused once settled, because finished accounts must stay
    auditable, and once anybody has paid into it, because a payment is
    cash the organizer really received.
    """
    season = db.get(SeasonRow, season_id)
    if season is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No season with id {season_id}")
    _require_organizer(db, season.club_id, current_player)
    if season.settled_at is not None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "This season is settled — its books can't be deleted",
        )
    paid_into = (
        db.query(LedgerEntryRow.id)
        .filter(
            LedgerEntryRow.season_id == season_id,
            LedgerEntryRow.entry_type == EntryType.PAYMENT,
        )
        .first()
    )
    if paid_into is not None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "This season has payments recorded — it can't be deleted",
        )

    _audit(
        db,
        current_player,
        "season_deleted",
        season.club_id,
        detail=_season_span(db, season),
    )
    _delete_season_rows(db, season)
    db.commit()


@router.get("/clubs/{club_id}/seasons", response_model=list[SeasonSummaryOut])
def list_seasons(
    club_id: int,
    db: Session = Depends(get_db),
    current_player: PlayerRow = Depends(get_current_player),
) -> list[SeasonSummaryOut]:
    """Each season's id and dates, enough to label it in a picker.
    Members only.
    """
    _get_club_or_404(db, club_id)
    _require_club_access(db, club_id, current_player)
    season_rows = (
        db.query(SeasonRow)
        .filter(SeasonRow.club_id == club_id)
        .order_by(SeasonRow.id.desc())
        .all()
    )
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


@router.post("/clubs/{club_id}/seasons", response_model=SeasonOut)
def start_season(
    club_id: int,
    payload: SeasonCreate,
    db: Session = Depends(get_db),
    current_player: PlayerRow = Depends(get_current_player),
) -> SeasonOut:
    _get_club_or_404(db, club_id)
    _require_organizer(db, club_id, current_player)

    # A fixed member holds a slot at every game, so the starting roster
    # cannot exceed capacity (the same rule add_member enforces later).
    if len(payload.member_names) > payload.capacity:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"{len(payload.member_names)} fixed members is more than the "
            f"capacity of {payload.capacity}",
        )

    season = SeasonRow(
        club_id=club_id,
        total_venue_cost=payload.total_venue_cost,
        ac_surcharge=payload.ac_surcharge,
        capacity=payload.capacity,
        minimum_roster=payload.minimum_roster,
        game_start_time=payload.game_start_time,
        game_end_time=payload.game_end_time,
        location=payload.location,
        change_deadline_hours=payload.change_deadline_hours,
    )
    db.add(season)
    db.flush()

    # Which nights are forecast to need the air conditioning; each game can
    # be corrected on the night.
    cooled = set(payload.air_conditioned_dates)
    games = [
        GameRow(season_id=season.id, date=d, air_conditioned=d in cooled)
        for d in payload.game_dates
    ]
    db.add_all(games)

    # A roster is a set: two names resolving to the same person add them
    # once.
    member_ids = []
    for name in payload.member_names:
        player = _get_or_create_player(db, club_id, name)
        if player.id in member_ids:
            continue
        db.add(SeasonMemberRow(season_id=season.id, player_id=player.id))
        member_ids.append(player.id)

    db.flush()
    _sync_season_fee_ledger(db, season)
    _audit(
        db,
        current_player,
        "season_started",
        season.club_id,
        amount=season.total_venue_cost,
        detail=_season_span(db, season),
    )

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
        change_deadline_hours=season.change_deadline_hours,
        games=[GameOut(id=g.id, date=g.date, status=g.status) for g in games],
        member_ids=member_ids,
    )


def _season_out(db: Session, season: SeasonRow) -> SeasonOut:
    games = (
        db.query(GameRow)
        .filter(GameRow.season_id == season.id)
        .order_by(GameRow.date)
        .all()
    )
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
        change_deadline_hours=season.change_deadline_hours,
        games=[GameOut(id=g.id, date=g.date, status=g.status) for g in games],
        member_ids=member_ids,
    )


@router.patch("/seasons/{season_id}", response_model=SeasonOut)
def update_season(
    season_id: int,
    payload: SeasonUpdate,
    db: Session = Depends(get_db),
    current_player: PlayerRow = Depends(get_current_player),
) -> SeasonOut:
    """Changes only the fields the client sent (see SeasonUpdate).

    A new venue cost or capacity re-prices every game of the season,
    so each member's season-fee charge is corrected with an adjustment
    entry (see _sync_season_fee_ledger), and both are locked once the
    season is settled.
    """
    season = db.get(SeasonRow, season_id)
    if season is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No season with id {season_id}")
    _require_organizer(db, season.club_id, current_player)

    updates = payload.model_dump(exclude_unset=True)
    before = {
        "total_venue_cost": season.total_venue_cost,
        "ac_surcharge": season.ac_surcharge,
        "capacity": season.capacity,
    }
    # All three move the per-game share, so none may change once settled.
    priced = {"total_venue_cost", "ac_surcharge", "capacity"} & updates.keys()
    if priced and season.settled_at is not None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Season is already settled — venue cost can't change now",
        )

    # Capacity cannot drop below the roster size or below any game's
    # expected attendance.
    if "capacity" in updates and updates["capacity"] < season.capacity:
        new_capacity = updates["capacity"]
        roster_size = (
            db.query(SeasonMemberRow)
            .filter(SeasonMemberRow.season_id == season.id)
            .count()
        )
        games = db.query(GameRow).filter(GameRow.season_id == season.id).all()
        busiest = max((_expected_on_court(db, g, season) for g in games), default=0)
        floor = max(roster_size, busiest)
        if new_capacity < floor:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"Can't lower capacity to {new_capacity} — {floor} people "
                "are already on the roster or on a game's court",
            )

    raised = "capacity" in updates and updates["capacity"] > season.capacity
    for field, value in updates.items():
        setattr(season, field, value)

    if priced:
        db.flush()
        _sync_season_fee_ledger(db, season)

    # A higher capacity opens slots, so the queue is offered them at games
    # still to come. Done after the fee sync, so each promotion is charged
    # at the new share.
    if raised:
        _offer_freed_slots_to_the_queue(db, season)

    # Changes that re-price the season are audited with their old value.
    for field, action in (
        ("total_venue_cost", "venue_cost_changed"),
        ("ac_surcharge", "ac_surcharge_changed"),
        ("capacity", "capacity_changed"),
    ):
        if field in updates and updates[field] != before[field]:
            _audit(
                db,
                current_player,
                action,
                season.club_id,
                amount=getattr(season, field),
                detail=str(before[field]),
            )

    db.commit()
    db.refresh(season)
    return _season_out(db, season)


@router.put("/seasons/{season_id}/air-conditioned-dates", status_code=204)
def set_air_conditioned_dates(
    season_id: int,
    payload: AirConditionedDatesUpdate,
    db: Session = Depends(get_db),
    current_player: PlayerRow = Depends(get_current_player),
) -> None:
    """Corrects which nights the season is forecast to need the air
    conditioning.

    The total venue cost stays the same; only which nights carry the
    AC portion moves, which re-prices each game. Rounding makes a
    member's season fee shift by a few dollars, written as an
    adjustment entry. Refused once settled, and for a game already
    played: what happened on the night is recorded with
    games.set_game_air_conditioning, so the same night is never
    adjusted both ways.
    """
    season = db.get(SeasonRow, season_id)
    if season is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No season with id {season_id}")
    _require_organizer(db, season.club_id, current_player)
    if season.settled_at is not None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Season is already settled")

    cooled = set(payload.dates)
    games = db.query(GameRow).filter(GameRow.season_id == season_id).all()
    unknown = cooled - {game.date for game in games}
    if unknown:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"No game on {min(unknown)} in this season",
        )
    today = _today_in_taiwan()
    played = sorted(
        game.date
        for game in games
        if game.date < today and (game.date in cooled) != game.air_conditioned
    )
    if played:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"The game on {played[0]} has been played — record whether the "
            "air conditioning ran on the night instead",
        )
    for game in games:
        game.air_conditioned = game.date in cooled
    db.flush()
    _sync_season_fee_ledger(db, season)
    _audit(
        db,
        current_player,
        "ac_forecast_corrected",
        season.club_id,
        detail=str(len(cooled)),
    )
    db.commit()


@router.post(
    "/seasons/{season_id}/air-conditioned-dates/preview",
    response_model=AirConditionedPreviewOut,
)
def preview_air_conditioned_dates(
    season_id: int,
    payload: AirConditionedDatesUpdate,
    db: Session = Depends(get_db),
    current_player: PlayerRow = Depends(get_current_player),
) -> AirConditionedPreviewOut:
    """What correcting the forecast would do to each game's price and to
    a member's season fee, without saving anything. Uses the same
    pricing code as the books, so the preview cannot disagree with the
    ledger.
    """
    season_row = db.get(SeasonRow, season_id)
    if season_row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No season with id {season_id}")
    _require_organizer(db, season_row.club_id, current_player)

    game_rows = (
        db.query(GameRow)
        .filter(GameRow.season_id == season_id)
        .order_by(GameRow.date)
        .all()
    )
    cooled = set(payload.dates)
    unknown = cooled - {game.date for game in game_rows}
    if unknown:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"No game on {min(unknown)} in this season",
        )
    member_rows = (
        db.query(PlayerRow)
        .join(SeasonMemberRow, SeasonMemberRow.player_id == PlayerRow.id)
        .filter(SeasonMemberRow.season_id == season_id)
        .all()
    )
    before = season_from_rows(season_row, game_rows, member_rows)
    after = replace(
        before,
        games=tuple(
            replace(game, air_conditioned=game.date in cooled) for game in before.games
        ),
    )
    try:
        shares_before = season_shares(before)
        shares_after = season_shares(after)
    except ValueError as e:
        # More nights cooled than the venue bill can carry.
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from e

    def fee(shares: dict[int, Decimal], season: Season) -> Decimal:
        return sum(
            (
                shares[game.id]
                for game in season.games
                if game.status != GameStatus.CANCELLED_REFUNDED
            ),
            Decimal("0"),
        )

    return AirConditionedPreviewOut(
        season_fee_before=fee(shares_before, before),
        season_fee_after=fee(shares_after, after),
        games=[
            AirConditionedPreviewGame(
                date=game.date,
                air_conditioned=game.air_conditioned,
                share_before=shares_before[game.id],
                share_after=shares_after[game.id],
            )
            for game in after.games
        ],
    )


@router.get("/seasons/{season_id}", response_model=SeasonDetailOut)
def get_season(
    season_id: int,
    db: Session = Depends(get_db),
    current_player: PlayerRow = Depends(get_current_player),
) -> SeasonDetailOut:
    """The whole season: roster, every game, who is away, who is playing,
    who is queued. Members of the club only.
    """
    season_row = db.get(SeasonRow, season_id)
    if season_row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No season with id {season_id}")
    _require_club_access(db, season_row.club_id, current_player)

    # "locked" is per caller: the organizer is never locked out.
    viewer_is_organizer = _runs_club(db, season_row.club_id, current_player)

    game_rows = (
        db.query(GameRow)
        .filter(GameRow.season_id == season_id)
        .order_by(GameRow.date)
        .all()
    )
    game_ids = [g.id for g in game_rows]
    member_rows = (
        db.query(PlayerRow)
        .join(SeasonMemberRow, SeasonMemberRow.player_id == PlayerRow.id)
        .filter(SeasonMemberRow.season_id == season_id)
        .all()
    )
    notified_at = {
        player_id: sent_at
        for player_id, sent_at in db.query(
            SeasonMemberRow.player_id, SeasonMemberRow.fee_notice_sent_at
        ).filter(SeasonMemberRow.season_id == season_id)
    }
    previous = _previous_season(db, season_row)

    # One query per kind of attendance for the whole season, grouped by game
    # in Python, instead of one per game. Absences and drop-ins are ordered
    # earliest first so they can be paired FIFO for display, by the same
    # rule settlement.covered_absences uses for refunds.
    absences_by_game: dict[int, list[tuple[int, str]]] = defaultdict(list)
    # The caller's own absences: a 代打 covering one of them is theirs to
    # cancel.
    my_absence_ids: set[int] = set()
    # Profile pictures by row, kept apart from the tuples below.
    avatar_of_absence: dict[int, str | None] = {}
    avatar_of_drop_in: dict[int, str | None] = {}
    for absence, player in (
        db.query(AbsenceRow, PlayerRow)
        .join(PlayerRow, AbsenceRow.player_id == PlayerRow.id)
        # Members still on the roster only: "away" is about somebody
        # expected here.
        .join(
            SeasonMemberRow,
            and_(
                SeasonMemberRow.player_id == AbsenceRow.player_id,
                SeasonMemberRow.season_id == season_row.id,
            ),
        )
        .filter(AbsenceRow.game_id.in_(game_ids), AbsenceRow.cancelled_at.is_(None))
        .order_by(AbsenceRow.recorded_at)
        .all()
    ):
        absences_by_game[absence.game_id].append((absence.id, player.name))
        avatar_of_absence[absence.id] = player.avatar_url
        if absence.player_id == current_player.id:
            my_absence_ids.add(absence.id)

    # (drop_in_id, player_id, name, gender, covers_absence_id, brought_by,
    #  bringer_name)
    drop_ins_by_game: dict[
        int,
        list[tuple[int, int, str, Gender | None, int | None, int | None, str | None]],
    ] = defaultdict(list)
    # outerjoin: brought_by_player_id is null for everyone who signed
    # themselves up.
    Bringer = aliased(PlayerRow)
    for drop_in, player, bringer_name in (
        db.query(DropInRow, PlayerRow, Bringer.name)
        .join(PlayerRow, DropInRow.player_id == PlayerRow.id)
        .outerjoin(Bringer, Bringer.id == DropInRow.brought_by_player_id)
        .filter(DropInRow.game_id.in_(game_ids), DropInRow.playing())
        .order_by(DropInRow.signed_up_at)
        .all()
    ):
        avatar_of_drop_in[drop_in.id] = player.avatar_url
        drop_ins_by_game[drop_in.game_id].append(
            (
                drop_in.id,
                player.id,
                player.name,
                _gender(player.gender),
                drop_in.covers_absence_id,
                drop_in.brought_by_player_id,
                bringer_name,
            )
        )

    waitlist_by_game: dict[int, list[DropInSummary]] = defaultdict(list)
    # outerjoin for the same reason as the drop-ins above.
    for entry, player, queuer_name in (
        db.query(DropInRow, PlayerRow, Bringer.name)
        .join(PlayerRow, DropInRow.player_id == PlayerRow.id)
        .outerjoin(Bringer, Bringer.id == DropInRow.brought_by_player_id)
        .filter(DropInRow.game_id.in_(game_ids), DropInRow.queued())
        .order_by(DropInRow.queued_at, DropInRow.id)
        .all()
    ):
        waitlist_by_game[entry.game_id].append(
            DropInSummary(
                id=entry.id,
                player_name=player.name,
                gender=_gender(player.gender),
                signed_up_by_me=(
                    entry.player_id == current_player.id
                    or entry.brought_by_player_id == current_player.id
                ),
                brought_by_name=queuer_name,
                avatar_url=player.avatar_url,
            )
        )

    # Each game's price, from the same function the ledger uses.
    shares = season_shares(season_from_rows(season_row, game_rows, member_rows))

    games = []
    for game in game_rows:
        # (absence_id, name) pairs, FIFO order
        absences_list = absences_by_game[game.id]
        # (drop_in_id, player_id, name, gender, covers_absence_id) tuples
        drop_ins = drop_ins_by_game[game.id]

        # Two separate questions. Who a member arranged to stand in for them
        # (covers_absence_id) is a relationship between two people. Whether
        # an absence is refunded depends on anybody filling the slot,
        # matched FIFO. Somebody who came off the queue is never shown as a
        # member's 代打.
        absence_name_by_id = dict(absences_list)
        arranged_for_absence: dict[str, str] = {}
        arranged_by_drop_in: dict[int, str] = {}
        claimed_absence_ids: set[int] = set()
        for entry in drop_ins:
            (
                drop_in_id,
                _player_id,
                name,
                _drop_in_gender,
                covers_absence_id,
                _by,
                _bringer,
            ) = entry
            if covers_absence_id in absence_name_by_id:
                absence_name = absence_name_by_id[covers_absence_id]
                arranged_for_absence[absence_name] = name
                arranged_by_drop_in[drop_in_id] = absence_name
                claimed_absence_ids.add(covers_absence_id)

        fifo_absences = [
            (aid, name) for aid, name in absences_list if aid not in claimed_absence_ids
        ]
        fifo_drop_ins = [
            name
            for (
                _drop_in_id,
                _player_id,
                name,
                _gender,
                covers,
                _by,
                _bringer,
            ) in drop_ins
            if covers is None
        ]
        # Who is standing in the slot, which also decides the refund.
        filled_by: dict[int, str] = {
            aid: arranged_for_absence[absence_name_by_id[aid]]
            for aid in claimed_absence_ids
        }
        for i, (aid, _absence_name) in enumerate(fifo_absences):
            if i < len(fifo_drop_ins):
                filled_by[aid] = fifo_drop_ins[i]

        games.append(
            GameDetailOut(
                id=game.id,
                date=game.date,
                status=game.status,
                air_conditioned=game.air_conditioned,
                share=shares[game.id],
                # Null means the season's own venue.
                location=game.location,
                start_time=game.start_time,
                end_time=game.end_time,
                venue_cost_delta=game.venue_cost_delta,
                locked=not (
                    viewer_is_organizer or _within_change_deadline(game, season_row)
                ),
                past_deadline=not _within_change_deadline(game, season_row),
                absences=[
                    AbsenceDetailOut(
                        id=aid,
                        player_name=name,
                        covered_by=arranged_for_absence.get(name),
                        filled_by=filled_by.get(aid),
                        avatar_url=avatar_of_absence.get(aid),
                    )
                    for aid, name in absences_list
                ],
                confirmed_drop_ins=[
                    DropInDetailOut(
                        id=drop_in_id,
                        player_id=player_id,
                        player_name=name,
                        gender=gender,
                        covering=arranged_by_drop_in.get(drop_in_id),
                        # Themselves or a guest they brought: whose signup
                        # the screen may offer to cancel. The server checks
                        # again.
                        signed_up_by_me=(
                            player_id == current_player.id
                            or brought_by == current_player.id
                            or covers in my_absence_ids
                        ),
                        brought_by_name=bringer_name,
                        avatar_url=avatar_of_drop_in.get(drop_in_id),
                    )
                    for (
                        drop_in_id,
                        player_id,
                        name,
                        gender,
                        covers,
                        brought_by,
                        bringer_name,
                    ) in drop_ins
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
        change_deadline_hours=season_row.change_deadline_hours,
        # What one game costs one person before air conditioning, divided by
        # capacity like every share.
        share_per_game=share_per_game(
            season_row.total_venue_cost
            - season_row.ac_surcharge * sum(1 for g in game_rows if g.air_conditioned),
            len(game_rows),
            season_row.capacity,
        ),
        ac_surcharge=season_row.ac_surcharge,
        settled_at=season_row.settled_at,
        previous_season_settled=previous is None or previous.settled_at is not None,
        members=[
            MemberOut(
                id=m.id,
                name=m.name,
                gender=_gender(m.gender),
                avatar_url=m.avatar_url,
                linked=m.line_user_id is not None,
                fee_notice_sent_at=notified_at.get(m.id),
            )
            for m in member_rows
        ],
        games=games,
    )
