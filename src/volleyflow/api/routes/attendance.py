"""Taking leave, signing up, the queue, and standing in for
somebody.
"""

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
    _give_back_queue_place,
    _has_open_slot,
    _make_room_for_substitute,
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
    AbsenceRow,
    ClubMemberRow,
    DropInRow,
    GameRow,
    PlayerRow,
    SeasonMemberRow,
    SeasonRow,
    WaitlistEntryRow,
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
def cancel_absence(
    absence_id: int,
    db: Session = Depends(get_db),
    current_player: PlayerRow = Depends(get_current_player),
) -> AbsenceCancelOut:
    """The member is attending after all.

    Anybody standing in the slot goes back to the queue, at the position
    they originally held, and their fee comes off. That restores exactly
    the state before the absence was recorded, which is the fair reading:
    the slot only opened because this member released it, so taking the
    release back closes it again. The stand-in is no worse off than if
    the absence had never happened, and keeps their place ahead of anyone
    who queued later.

    This used to be refused outright — "someone is already covering,
    ask the organizer" — which was a dead end, because the app offers no
    way to ask. The member was simply stuck, on a game they had said
    they could play. Changed 2026-09-10 after that was reported.

    The change deadline still applies, and is what stops this being used
    to shuffle people in and out on the night.
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
    """A member (or the organizer) personally arranging — or later
    changing — who covers a specific absence, a "代打". Always
    confirmed, never queued: filling your own slot with someone you
    picked isn't competing with the waitlist for open capacity. See
    attendance.DropIn.covers.

    "Self" here means the absent member — the one whose slot is being
    covered, not the substitute being named — matching who's actually
    allowed to arrange this per CLAUDE.md 2.3.

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
    _require_self_or_organizer(db, season.club_id, current_player, absence.player_id)
    _require_season_open(season)

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
        # They did not withdraw — somebody else was picked instead — so
        # if they had left the queue to take this slot, they get that
        # place back. See _give_back_queue_place.
        _give_back_queue_place(db, existing)
        # Flush now, before the new DropInRow below is added: SQLAlchemy's
        # unit of work orders all pending INSERTs before UPDATEs regardless
        # of the order they were issued in, so without this the new row
        # (e.g. re-assigning the same person) would be inserted while the
        # old one is still active, tripping the active-substitute unique
        # index.
        db.flush()

    player = _get_or_create_player(db, season.club_id, payload.player_name)
    # The same rule the ordinary signup applies, and it was missing here.
    # A typed name resolves to an existing person when one in this club
    # already has it, so naming somebody who has a LINE account put the
    # real them on the roster — and on the hook for the fee — with no say
    # in it. A guest with no account is still fair game: they cannot sign
    # themselves up, so somebody has to.
    _require_may_sign_up(db, season.club_id, current_player, player)
    if player.gender is None and payload.gender is not None:
        player.gender = payload.gender

    # A fixed member is expected at this game already (CLAUDE.md 2.3), so
    # they cannot also stand in for somebody — they would be on the court
    # twice and pay for the night twice, once inside their season fee and
    # once as a drop-in. The ordinary signup route refuses this
    # (_reject_if_already_playing) and this one did not, which is how a
    # random sweep in tests/api/test_fuzz.py landed the same name in the
    # attending list twice. Being away themselves makes no difference: a
    # member who wants to play cancels their own absence, which fills
    # their own slot and leaves this one still open.
    if db.get(SeasonMemberRow, {"season_id": season.id, "player_id": player.id}):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"{player.name} is a fixed member of this season and is already "
            "expected — they can't stand in for somebody else",
        )

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

    # A queued person named as the substitute leaves the queue: they are
    # on the court now. Without this they appeared in both lists at once,
    # counted once and waiting once — reported from real use. The place
    # they gave up is remembered on the signup, so cancelling the
    # arrangement can hand it back rather than deleting them.
    queued = (
        db.query(WaitlistEntryRow)
        .filter(
            WaitlistEntryRow.player_id == player.id,
            WaitlistEntryRow.game_id == game.id,
        )
        .first()
    )
    came_from_queue_at = queued.queued_at if queued is not None else None
    if queued is not None:
        db.delete(queued)
        db.flush()

    displaced_player_id = _make_room_for_substitute(db, game, season)

    drop_in = DropInRow(
        player_id=player.id,
        game_id=game.id,
        signed_up_at=_now(),
        covers_absence_id=absence_id,
        from_waitlist_at=came_from_queue_at,
        # The member whose slot this is, not whoever tapped the button.
        # A 代打 is usually a friend with no account who will never open
        # the app or pay through it — the member who arranged them hands
        # the money over. Without this the money screen showed "Zoe owes
        # $235" with nothing to say who to ask, which is exactly the
        # "應該要跟某某某代打的人收帳" report. Same column the ordinary
        # +1 signup fills in.
        brought_by_player_id=absence.player_id,
    )
    db.add(drop_in)
    _record_drop_in_charge(db, drop_in, season, reverse=False)
    db.commit()
    db.refresh(drop_in)

    return DropInOut(
        status="confirmed",
        id=drop_in.id,
        player_id=player.id,
        game_id=game.id,
        displaced_player_id=displaced_player_id,
    )


@router.post("/drop-ins", response_model=DropInOut)
def sign_up(
    payload: DropInCreate,
    db: Session = Depends(get_db),
    current_player: PlayerRow = Depends(get_current_player),
) -> DropInOut:
    game = _get_game_or_404(db, payload.game_id)
    season = db.get(SeasonRow, game.season_id)
    assert season is not None  # game.season_id is a foreign key, always valid
    player = _get_or_create_player(db, season.club_id, payload.player_name)
    _require_may_sign_up(db, season.club_id, current_player, player)
    _require_season_open(season)
    _require_within_change_deadline(db, game, season, current_player)
    if player.gender is None and payload.gender is not None:
        player.gender = payload.gender

    _reject_if_already_playing(db, game, season, player)

    # Who put them on the list, when it wasn't themselves — recorded the
    # same way the batch signup does. Without it this endpoint left the
    # column null, so the money screen couldn't say who to collect from
    # and nothing could tell whose signup it was safe to offer a cancel
    # for.
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

    entry = WaitlistEntryRow(player_id=player.id, game_id=game.id, queued_at=_now())
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
    """ "+1, and I'm bringing two friends" — the whole group in one go.

    Everything happens inside the one transaction and behind the one
    `SELECT ... FOR UPDATE` that _get_game_or_404 takes, which is the
    reason this exists rather than the caller looping over POST
    /drop-ins: three separate requests can interleave with somebody
    else's signup and push the game past capacity, and they can also
    half-succeed, leaving some of a group charged and the rest not.

    Capacity is spent in list order, so the people who overflow are the
    ones the caller listed last — the app shows that before sending, so
    nobody is surprised by which of their friends got the waitlist.
    """
    game = _get_game_or_404(db, game_id)
    season = db.get(SeasonRow, game.season_id)
    assert season is not None  # game.season_id is a foreign key, always valid
    _require_season_open(season)
    _require_within_change_deadline(db, game, season, current_player)

    try:
        results = _sign_up_each(db, game, season, payload.people, current_player)
    except Exception:
        # Rows are flushed as we go, so that capacity sees each person as
        # it's added. That makes "all or nothing" this route's own job:
        # relying on the request-scoped session being closed to discard
        # them would leave the guarantee resting on something outside
        # the route, and any caller holding the session open — the tests
        # do — would see a half-finished group.
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
            # A bare name is always a new person; see DropInBatchEntry.
            player = PlayerRow(name=entry.player_name.strip(), gender=entry.gender)
            db.add(player)
            db.flush()  # assigns player.id without ending the transaction
            db.add(
                ClubMemberRow(
                    club_id=season.club_id,
                    player_id=player.id,
                    role="member",
                    joined_at=_now(),
                )
            )
        else:
            player = _get_player_or_404(db, entry.player_id)
            _require_club_access(db, season.club_id, player)
            if player.gender is None and entry.gender is not None:
                player.gender = entry.gender
        _require_may_sign_up(db, season.club_id, current_player, player)
        _reject_if_already_playing(db, game, season, player)

        # Null for anyone signing themselves up: the field answers "who
        # do I collect this from", and for yourself that's already you.
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
            # _has_open_slot counts rows, so the next person in this
            # batch only sees the slot as taken once this one is flushed.
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
            wait = WaitlistEntryRow(
                player_id=player.id, game_id=game.id, queued_at=_now()
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
    """Give up a place in the queue.

    This did not exist, and the frontend was sending waitlist entry ids
    to /drop-ins/{id}/cancel instead. `waitlist_entries.id` and
    `drop_ins.id` are independent sequences, so that either 404'd, or —
    when the numbers happened to line up — cancelled a completely
    different person's confirmed signup. Queued people were stuck: they
    could not leave, and signing up again was refused as a duplicate.

    Deleted rather than marked cancelled, unlike a drop-in: a queue
    place carries no money and no history worth keeping, and the
    position of everyone behind them is simply their `queued_at` order.
    """
    entry = db.get(WaitlistEntryRow, entry_id)
    if entry is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, f"No waitlist entry with id {entry_id}"
        )
    game = _get_game_or_404(db, entry.game_id)
    season = db.get(SeasonRow, game.season_id)
    assert season is not None  # game.season_id is a foreign key, always valid
    _require_self_or_organizer(db, season.club_id, current_player, entry.player_id)
    _require_season_open(season)
    _require_within_change_deadline(db, game, season, current_player)

    player_id = entry.player_id
    db.delete(entry)
    db.commit()
    return WaitlistCancelOut(id=entry_id, player_id=player_id, game_id=game.id)


@router.post("/waitlist/{entry_id}/promote", response_model=WaitlistPromoteOut)
def promote_from_waitlist(
    entry_id: int,
    payload: WaitlistPromote,
    db: Session = Depends(get_db),
    current_player: PlayerRow = Depends(get_current_player),
) -> WaitlistPromoteOut:
    """Put a specific queued person on the court. Organizer only.

    The queue's order decides who gets a slot that opens by itself. It
    can't decide who *should* play, because it doesn't know that the
    first person in it is away this week — the organizer does, usually
    from a message in the group chat. Without this the only way to act
    on that was to cancel people one at a time until the automatic
    promotion happened to land on the right person, charging and
    refunding everyone it stepped through on the way.

    The capacity cap still holds: with the game full, bringing somebody
    in means naming who comes out, and both happen in this one
    transaction.
    """
    entry = db.get(WaitlistEntryRow, entry_id)
    if entry is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, f"No waitlist entry with id {entry_id}"
        )
    # Locks the game for the rest of the transaction, so the slot this
    # decision is based on can't be taken by a concurrent signup.
    game = _get_game_or_404(db, entry.game_id)
    season = db.get(SeasonRow, game.season_id)
    assert season is not None  # game.season_id is a foreign key, always valid
    _require_organizer(db, season.club_id, current_player)
    _require_season_open(season)

    replaced_player_id: int | None = None
    if payload.replacing_drop_in_id is not None:
        replaced = db.get(DropInRow, payload.replacing_drop_in_id)
        if replaced is None or replaced.game_id != game.id:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND,
                f"No drop-in with id {payload.replacing_drop_in_id} in this game",
            )
        if replaced.cancelled_at is not None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Already cancelled")
        replaced_player_id = replaced.player_id
        replaced.cancelled_at = _now()
        _record_drop_in_charge(db, replaced, season, reverse=True)
        # Back into the queue, at the time they originally joined it.
        # They did not withdraw — the organizer picked somebody else —
        # so dropping them entirely would quietly delete a person who is
        # still waiting to play, and would make the screen's "X 回到候補"
        # a lie. Matches _make_room_for_substitute.
        db.add(
            WaitlistEntryRow(
                player_id=replaced.player_id,
                game_id=game.id,
                queued_at=replaced.signed_up_at,
            )
        )
        db.flush()
        # Deliberately not _promote_from_waitlist here: that is the rule
        # for a slot opening on its own, and this slot is already spoken
        # for. Running it would put the queue's first person in the seat
        # the organizer just chose somebody else for.
    elif not _has_open_slot(db, game, season):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"This game is full ({season.capacity}). "
            "Name who comes out, or raise the capacity first.",
        )

    drop_in = _promote_entry(db, entry)
    db.commit()
    db.refresh(drop_in)

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
    if drop_in is None:
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

    drop_in.cancelled_at = _now()
    _record_drop_in_charge(db, drop_in, season, reverse=True)

    # A place in the queue is given up, not lost. Somebody taken off the
    # court by another person — a member cancelling the 代打 they
    # arranged, the organizer clearing a row — never said they couldn't
    # come, so if they had a place in the queue they get it back, at the
    # position they held. Naming the third person in the queue as your
    # substitute and then changing your mind used to delete them from
    # the game entirely.
    #
    # Only somebody who actually held one: a substitute typed in by name
    # was never waiting, and putting them into a queue they never joined
    # would resurrect them into the next open slot. That is what
    # from_waitlist_at records, because it cannot be inferred.
    #
    # Cancelling your own signup is a withdrawal and never re-queues you.
    #
    # Re-queued *before* the promotion below, not after, so the freed
    # slot still goes to whoever is genuinely first. If that turns out
    # to be this same person, they simply keep playing — as an ordinary
    # 臨打 rather than somebody's arranged substitute, which is exactly
    # what they now are.
    if current_player.id != drop_in.player_id:
        _give_back_queue_place(db, drop_in)

    promoted = _promote_from_waitlist(db, drop_in.game_id)

    db.commit()
    db.refresh(drop_in)

    return DropInCancelOut(
        id=drop_in.id,
        cancelled_at=drop_in.cancelled_at,
        promoted_from_waitlist=promoted,
    )
