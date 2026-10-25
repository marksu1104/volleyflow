"""Taking leave, signing up, the queue, and standing in for somebody."""

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    status,
)
from sqlalchemy.orm import Session

from volleyflow.api.dependencies import get_db
from volleyflow.api.routes._attendance import (
    _back_to_the_queue,
    _get_game_or_404,
    _has_open_slot,
    _make_room_for_substitute,
    _off_the_court,
    _promote_entry,
    _promote_from_waitlist,
    _reject_if_already_playing,
    _release_whoever_is_covering,
    _require_may_cancel_drop_in,
    _require_season_member,
    _require_season_open,
    _require_within_change_deadline,
)
from volleyflow.api.routes._money import (
    _record_drop_in_charge,
)
from volleyflow.api.routes._people import (
    _get_or_create_player,
    _get_player_by_name,
    _get_player_or_404,
    _now,
    _require_club_access,
    _require_may_sign_up,
    _require_organizer,
    _require_self_or_organizer,
    get_current_player,
)
from volleyflow.api.schemas import (
    AbsenceCancelOut,
    AbsenceCreate,
    AbsenceOut,
    DropInBatchCreate,
    DropInBatchEntry,
    DropInBatchOut,
    DropInCancelOut,
    DropInCreate,
    DropInOut,
    SubstituteCreate,
    WaitlistCancelOut,
    WaitlistPromote,
    WaitlistPromoteOut,
)
from volleyflow.db.models import (
    PLAYING,
    QUEUED,
    AbsenceRow,
    ClubMemberRow,
    DropInRow,
    GameRow,
    PlayerRow,
    SeasonMemberRow,
    SeasonRow,
)

router = APIRouter()


@router.post("/absences", response_model=AbsenceOut)
def record_absence(
    payload: AbsenceCreate,
    db: Session = Depends(get_db),
    current_player: PlayerRow = Depends(get_current_player),
) -> AbsenceOut:
    game = _get_game_or_404(db, payload.game_id)
    season = db.get(SeasonRow, game.season_id)
    assert season is not None
    player = _get_player_by_name(db, season.club_id, payload.player_name)
    _require_self_or_organizer(db, season.club_id, current_player, player.id)
    _require_season_member(db, game.season_id, player.id)
    _require_season_open(season)
    _require_within_change_deadline(db, game, season, current_player)

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

    # Naming the substitute in the same request gives the slot straight to
    # them, so nobody is promoted, charged and bumped a moment later. A
    # refused substitute rolls the absence back too.
    substitute = None
    promoted = None
    if payload.substitute is not None:
        db.flush()
        try:
            drop_in, displaced, sub_player = _name_substitute(
                db, absence, game, season, current_player, payload.substitute
            )
        except HTTPException:
            db.rollback()
            raise
        db.flush()
        substitute = DropInOut(
            status="confirmed",
            id=drop_in.id,
            player_id=sub_player.id,
            game_id=game.id,
            displaced_player_id=displaced,
        )
    else:
        promoted = _promote_from_waitlist(db, game.id)

    db.commit()
    db.refresh(absence)

    # Whoever came off the queue is told by the deadline job; see
    # reminders.send_promotion_notices.
    return AbsenceOut(
        id=absence.id,
        player_id=absence.player_id,
        game_id=absence.game_id,
        recorded_at=absence.recorded_at,
        promoted_from_waitlist=promoted,
        substitute=substitute,
    )


@router.post("/absences/{absence_id}/cancel", response_model=AbsenceCancelOut)
def cancel_absence(
    absence_id: int,
    db: Session = Depends(get_db),
    current_player: PlayerRow = Depends(get_current_player),
) -> AbsenceCancelOut:
    """The member is attending after all.

    Whoever was standing in the slot goes back to the queue at their
    original place, with their fee reversed: the slot opened only
    because this member released it, so taking the release back closes
    it. The change deadline still applies.
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
    _require_self_or_organizer(db, season.club_id, current_player, absence.player_id)
    _require_season_open(season)
    _require_within_change_deadline(db, game, season, current_player)

    released_player_id = _release_whoever_is_covering(db, absence, season)

    absence.cancelled_at = _now()
    db.commit()
    db.refresh(absence)

    return AbsenceCancelOut(
        id=absence.id,
        cancelled_at=absence.cancelled_at,
        released_player_id=released_player_id,
    )


@router.put("/absences/{absence_id}/substitute", response_model=DropInOut)
def set_substitute(
    absence_id: int,
    payload: SubstituteCreate,
    db: Session = Depends(get_db),
    current_player: PlayerRow = Depends(get_current_player),
) -> DropInOut:
    """Names (or replaces) the 代打 a member personally arranged for their
    absence. Always confirmed, never queued: filling your own slot
    does not compete with the queue.

    No change-deadline check, since the slot stays filled either way.
    Calling it again replaces the previous substitute, whose charge is
    reversed. Removing coverage altogether goes through
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
    _require_self_or_organizer(db, season.club_id, current_player, absence.player_id)
    _require_season_open(season)

    drop_in, displaced_player_id, player = _name_substitute(
        db, absence, game, season, current_player, payload
    )
    db.commit()
    db.refresh(drop_in)

    return DropInOut(
        status="confirmed",
        id=drop_in.id,
        player_id=player.id,
        game_id=game.id,
        displaced_player_id=displaced_player_id,
    )


def _name_substitute(
    db: Session,
    absence: AbsenceRow,
    game: GameRow,
    season: SeasonRow,
    current_player: PlayerRow,
    payload: SubstituteCreate,
) -> tuple[DropInRow, int | None, PlayerRow]:
    """Puts the named person on court in this absence's slot, replacing
    whoever was arranged before. Shared by PUT
    /absences/{id}/substitute and POST /absences with a substitute.

    Returns the substitute's drop-in, whoever went back to the queue
    to make room, and the substitute. The caller commits.
    """
    absence_id = absence.id
    existing = (
        db.query(DropInRow)
        .filter(DropInRow.covers_absence_id == absence_id, DropInRow.playing())
        .first()
    )
    if existing is not None:
        # They did not withdraw, so they get back any queue place they left
        # (see _off_the_court). Flushed inside it, before the new row below:
        # SQLAlchemy inserts before it updates, and the old row would
        # otherwise trip the one-row-per-person-per-game index.
        _off_the_court(db, existing, season)

    player = _get_or_create_player(db, season.club_id, payload.player_name)
    # Nobody may sign up a person who has a LINE account but themselves.
    _require_may_sign_up(db, season.club_id, current_player, player)
    if player.gender is None and payload.gender is not None:
        player.gender = payload.gender

    # A fixed member is already expected at this game, so they cannot also
    # stand in for somebody; an absent member who wants to play cancels
    # their own absence instead.
    if db.get(SeasonMemberRow, {"season_id": season.id, "player_id": player.id}):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"{player.name} is a fixed member of this season and is already "
            "expected — they can't stand in for somebody else",
        )

    # The previous substitute was taken off above, so any live signup here
    # is a different one.
    other_drop_in = (
        db.query(DropInRow)
        .filter(
            DropInRow.player_id == player.id,
            DropInRow.game_id == game.id,
            DropInRow.playing(),
        )
        .first()
    )
    if other_drop_in is not None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "That player is already signed up for this game",
        )

    # A queued person named as the substitute moves onto the court on their
    # own row, keeping when they queued and who signed them up.
    queued = (
        db.query(DropInRow)
        .filter(
            DropInRow.player_id == player.id,
            DropInRow.game_id == game.id,
            DropInRow.queued(),
        )
        .first()
    )

    displaced_player_id = _make_room_for_substitute(db, game, season)

    if queued is not None:
        drop_in = queued
        drop_in.status = PLAYING
        drop_in.signed_up_at = _now()
        drop_in.covers_absence_id = absence_id
    else:
        # Brought by whoever is naming them, not the absent member; see
        # DropInRow.brought_by_player_id.
        drop_in = DropInRow(
            player_id=player.id,
            game_id=game.id,
            signed_up_at=_now(),
            covers_absence_id=absence_id,
            brought_by_player_id=(
                None if player.id == current_player.id else current_player.id
            ),
        )
        db.add(drop_in)
    _record_drop_in_charge(db, drop_in, season, reverse=False)
    return drop_in, displaced_player_id, player


@router.post("/drop-ins", response_model=DropInOut)
def sign_up(
    payload: DropInCreate,
    db: Session = Depends(get_db),
    current_player: PlayerRow = Depends(get_current_player),
) -> DropInOut:
    game = _get_game_or_404(db, payload.game_id)
    season = db.get(SeasonRow, game.season_id)
    assert season is not None  # game.season_id is a foreign key, always valid
    # Checked before _get_or_create_player, which would otherwise create
    # somebody inside a club the caller has no part in.
    _require_club_access(db, season.club_id, current_player)
    player = _get_or_create_player(db, season.club_id, payload.player_name)
    _require_may_sign_up(db, season.club_id, current_player, player)
    _require_season_open(season)
    _require_within_change_deadline(db, game, season, current_player)
    if player.gender is None and payload.gender is not None:
        player.gender = payload.gender

    _reject_if_already_playing(db, game, season, player)

    # Who to collect the fee from, and who may cancel this signup for them.
    brought_by = None if player.id == current_player.id else current_player.id

    if _has_open_slot(db, game, season):
        drop_in = DropInRow(
            player_id=player.id,
            game_id=game.id,
            signed_up_at=_now(),
            brought_by_player_id=brought_by,
        )
        db.add(drop_in)
        _record_drop_in_charge(db, drop_in, season, reverse=False)
        db.commit()
        db.refresh(drop_in)
        return DropInOut(
            status="confirmed", id=drop_in.id, player_id=player.id, game_id=game.id
        )

    now = _now()
    entry = DropInRow(
        player_id=player.id,
        game_id=game.id,
        signed_up_at=now,
        queued_at=now,
        status=QUEUED,
        brought_by_player_id=brought_by,
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return DropInOut(
        status="waitlisted", id=entry.id, player_id=player.id, game_id=game.id
    )


@router.post("/games/{game_id}/drop-ins", response_model=DropInBatchOut)
def sign_up_several(
    game_id: int,
    payload: DropInBatchCreate,
    db: Session = Depends(get_db),
    current_player: PlayerRow = Depends(get_current_player),
) -> DropInBatchOut:
    """Signs up a group ("+1, and two friends") in one transaction under
    one game lock, so the group can neither overfill the game by
    interleaving with other signups nor half-succeed.

    Capacity is spent in list order: whoever overflows is listed last,
    and the app shows that before sending.
    """
    game = _get_game_or_404(db, game_id)
    season = db.get(SeasonRow, game.season_id)
    assert season is not None  # game.season_id is a foreign key, always valid
    _require_season_open(season)
    _require_within_change_deadline(db, game, season, current_player)
    # Settled before _sign_up_each starts creating people.
    _require_club_access(db, season.club_id, current_player)

    try:
        results = _sign_up_each(db, game, season, payload.people, current_player)
    except Exception:
        # Rows are flushed as they are added so capacity sees each one,
        # which makes rolling back on failure this route's own job.
        db.rollback()
        raise

    db.commit()
    return DropInBatchOut(results=results)


def _sign_up_each(
    db: Session,
    game: GameRow,
    season: SeasonRow,
    people: list[DropInBatchEntry],
    current_player: PlayerRow,
) -> list[DropInOut]:
    results: list[DropInOut] = []
    for entry in people:
        if entry.player_id is None:
            # One name, one person in a club; see DropInBatchEntry.
            player = _get_or_create_player(
                db, season.club_id, entry.player_name.strip()
            )
            if player.gender is None and entry.gender is not None:
                player.gender = entry.gender
        else:
            player = _get_player_or_404(db, entry.player_id)
            membership = db.get(
                ClubMemberRow, {"club_id": season.club_id, "player_id": player.id}
            )
            if membership is None:
                # The name as sent: an id alone mustn't reveal anyone's name.
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST,
                    f"{entry.player_name.strip()} is no longer in this club",
                )
            if player.gender is None and entry.gender is not None:
                player.gender = entry.gender
        _require_may_sign_up(db, season.club_id, current_player, player)
        _reject_if_already_playing(db, game, season, player)

        # Null when signing themselves up.
        brought_by = None if player.id == current_player.id else current_player.id

        if _has_open_slot(db, game, season):
            drop_in = DropInRow(
                player_id=player.id,
                game_id=game.id,
                signed_up_at=_now(),
                brought_by_player_id=brought_by,
            )
            db.add(drop_in)
            _record_drop_in_charge(db, drop_in, season, reverse=False)
            # Flushed so the next person sees this slot as taken.
            db.flush()
            results.append(
                DropInOut(
                    status="confirmed",
                    id=drop_in.id,
                    player_id=player.id,
                    game_id=game.id,
                )
            )
        else:
            now = _now()
            wait = DropInRow(
                player_id=player.id,
                game_id=game.id,
                signed_up_at=now,
                queued_at=now,
                status=QUEUED,
                brought_by_player_id=brought_by,
            )
            db.add(wait)
            db.flush()
            results.append(
                DropInOut(
                    status="waitlisted",
                    id=wait.id,
                    player_id=player.id,
                    game_id=game.id,
                )
            )
    return results


@router.post("/waitlist/{entry_id}/cancel", response_model=WaitlistCancelOut)
def leave_waitlist(
    entry_id: int,
    db: Session = Depends(get_db),
    current_player: PlayerRow = Depends(get_current_player),
) -> WaitlistCancelOut:
    """Gives up a place in the queue. The row is marked cancelled; no
    money moves, because a queued signup was never charged.
    """
    entry = db.get(DropInRow, entry_id)
    if entry is None or entry.status != QUEUED or entry.cancelled_at is not None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, f"No waitlist entry with id {entry_id}"
        )
    game = _get_game_or_404(db, entry.game_id)
    season = db.get(SeasonRow, game.season_id)
    assert season is not None  # game.season_id is a foreign key, always valid
    # Whoever queued a guest may take them back out.
    if entry.brought_by_player_id != current_player.id:
        _require_self_or_organizer(db, season.club_id, current_player, entry.player_id)
    _require_season_open(season)
    _require_within_change_deadline(db, game, season, current_player)

    player_id = entry.player_id
    entry.cancelled_at = _now()
    db.commit()
    return WaitlistCancelOut(id=entry_id, player_id=player_id, game_id=game.id)


@router.post("/waitlist/{entry_id}/promote", response_model=WaitlistPromoteOut)
def promote_from_waitlist(
    entry_id: int,
    payload: WaitlistPromote,
    db: Session = Depends(get_db),
    current_player: PlayerRow = Depends(get_current_player),
) -> WaitlistPromoteOut:
    """Puts a specific queued person on court. Organizer only.

    The queue's order decides who gets a slot that opens by itself;
    the organizer may know better (the first person in it is away this
    week). On a full game they must name who comes out, and both
    happen in one transaction.
    """
    entry = db.get(DropInRow, entry_id)
    if entry is None or entry.status != QUEUED or entry.cancelled_at is not None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, f"No waitlist entry with id {entry_id}"
        )
    # Locks the game, so a concurrent signup cannot take the slot decided on
    # here.
    game = _get_game_or_404(db, entry.game_id)
    season = db.get(SeasonRow, game.season_id)
    assert season is not None  # game.season_id is a foreign key, always valid
    _require_organizer(db, season.club_id, current_player)
    _require_season_open(season)

    replaced_player_id: int | None = None
    if payload.replacing_drop_in_id is not None:
        replaced = db.get(DropInRow, payload.replacing_drop_in_id)
        if (
            replaced is None
            or replaced.game_id != game.id
            or replaced.status != PLAYING
        ):
            raise HTTPException(
                status.HTTP_404_NOT_FOUND,
                f"No drop-in with id {payload.replacing_drop_in_id} in this game",
            )
        if replaced.cancelled_at is not None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Already cancelled")
        replaced_player_id = replaced.player_id
        # Back into the queue at their original place: the organizer picked
        # somebody else, they did not withdraw.
        _back_to_the_queue(db, replaced, season)
        # Not _promote_from_waitlist: this slot is already given to the
        # person the organizer chose.
    elif not _has_open_slot(db, game, season):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"This game is full ({season.capacity}). "
            "Name who comes out, or raise the capacity first.",
        )

    drop_in = _promote_entry(db, entry)
    db.commit()
    db.refresh(drop_in)

    # Told by the deadline job, like anyone promoted from the queue.
    return WaitlistPromoteOut(
        player_id=drop_in.player_id,
        game_id=game.id,
        drop_in_id=drop_in.id,
        replaced_player_id=replaced_player_id,
    )


@router.post("/drop-ins/{drop_in_id}/cancel", response_model=DropInCancelOut)
def cancel_drop_in(
    drop_in_id: int,
    db: Session = Depends(get_db),
    current_player: PlayerRow = Depends(get_current_player),
) -> DropInCancelOut:
    drop_in = db.get(DropInRow, drop_in_id)
    # Queued signups are left through /waitlist/{id}/cancel.
    if drop_in is None or drop_in.status != PLAYING:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, f"No drop-in with id {drop_in_id}"
        )
    if drop_in.cancelled_at is not None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Already cancelled")

    game = _get_game_or_404(db, drop_in.game_id)
    season = db.get(SeasonRow, game.season_id)
    assert season is not None
    _require_may_cancel_drop_in(db, drop_in, season, current_player)
    _require_season_open(season)
    _require_within_change_deadline(db, game, season, current_player)

    taken_at = _now()

    # Cancelling an ordinary signup is a withdrawal, whoever presses the
    # button for them. Cancelling an arranged substitute takes the slot away
    # from them instead, so they get back any queue place they left; the
    # substitute cancelling themselves is still a withdrawal.
    returning_arranged_substitute = (
        drop_in.covers_absence_id is not None and current_player.id != drop_in.player_id
    )
    if returning_arranged_substitute:
        _off_the_court(db, drop_in, season)
    else:
        drop_in.cancelled_at = taken_at
        _record_drop_in_charge(db, drop_in, season, reverse=True)

    promoted = _promote_from_waitlist(db, drop_in.game_id)

    db.commit()
    db.refresh(drop_in)

    return DropInCancelOut(
        id=drop_in.id,
        # When their place on court ended; the row may be back in the queue
        # rather than cancelled.
        cancelled_at=drop_in.cancelled_at or taken_at,
        promoted_from_waitlist=promoted,
    )
