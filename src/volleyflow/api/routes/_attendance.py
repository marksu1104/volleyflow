"""The rules about who is on court, and the helpers that move people on
and off it: capacity, deadlines, the queue, substitutes, and the
absorb/restore pairs that make adding and removing a fixed member
undoable. Calls down into _money to record what each move costs.
"""

from datetime import datetime
from decimal import Decimal

from fastapi import (
    HTTPException,
    status,
)
from sqlalchemy import and_, func
from sqlalchemy.orm import Session

from volleyflow.api.conversion import (
    absence_from_row,
    drop_in_from_row,
    game_from_row,
    player_from_row,
)
from volleyflow.api.routes._money import (
    _record_drop_in_charge,
)
from volleyflow.api.routes._people import (
    _now,
    _now_in_taiwan,
    _require_organizer,
    _runs_club,
    _today_in_taiwan,
)
from volleyflow.db.models import (
    PLAYING,
    QUEUED,
    AbsenceRow,
    DropInRow,
    GameRow,
    LedgerEntryRow,
    PlayerRow,
    SeasonMemberRow,
    SeasonRow,
)
from volleyflow.ledger import EntryType
from volleyflow.schedule import GameStatus, change_deadline
from volleyflow.settlement import (
    covered_absences,
)


def _reject_if_already_playing(
    db: Session, game: GameRow, season: SeasonRow, player: PlayerRow
) -> None:
    """Refuses a signup that would put someone on this game twice. Shared
    by the single and batch signup routes so both reject the same
    cases.
    """
    live = (
        db.query(DropInRow)
        .filter(
            DropInRow.player_id == player.id,
            DropInRow.game_id == game.id,
            DropInRow.cancelled_at.is_(None),
        )
        .first()
    )
    if live is not None and live.status == PLAYING:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"{player.name} is already signed up for this game",
        )
    if live is not None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"{player.name} is already on the waitlist for this game",
        )

    # A fixed member is already expected at every game; signing up again
    # would charge them twice and count them twice.
    fixed_member = db.get(
        SeasonMemberRow, {"season_id": season.id, "player_id": player.id}
    )
    if fixed_member is not None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"{player.name} is a fixed member of this season — no need to sign up",
        )


def _get_game_or_404(db: Session, game_id: int) -> GameRow:
    """Loads the game and locks its row for the rest of the transaction.

    Callers read the roster and then write based on it (is there a
    slot, who is next in the queue). SELECT ... FOR UPDATE makes a
    concurrent request for the same game wait, so two requests cannot
    both take the last slot. SQLite ignores the lock; the `postgres`
    tests exercise it.
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


def _expected_on_court(db: Session, game: GameRow, season: SeasonRow) -> int:
    """How many people this game expects: fixed members, minus members on
    leave, plus drop-ins playing.

    Independent of `season.capacity`, so a proposed capacity can be
    checked before it is saved.
    """
    member_count = (
        db.query(SeasonMemberRow).filter(SeasonMemberRow.season_id == season.id).count()
    )
    # Only live absences of members still on the roster: a cancelled absence
    # means they are coming, and a removed member is no longer counted in
    # member_count.
    absences = (
        db.query(AbsenceRow)
        .join(
            SeasonMemberRow,
            and_(
                SeasonMemberRow.player_id == AbsenceRow.player_id,
                SeasonMemberRow.season_id == season.id,
            ),
        )
        .filter(AbsenceRow.game_id == game.id, AbsenceRow.cancelled_at.is_(None))
        .count()
    )
    active_drop_ins = (
        db.query(DropInRow)
        .filter(DropInRow.game_id == game.id, DropInRow.playing())
        .count()
    )
    return (member_count - absences) + active_drop_ins


def _has_open_slot(db: Session, game: GameRow, season: SeasonRow) -> bool:
    return _expected_on_court(db, game, season) < season.capacity


def _games_with_no_room(
    db: Session, season: SeasonRow, *, member_count: int | None = None
) -> list[GameRow]:
    """The scheduled games where one more expected player would not fit.

    Adding a fixed member adds them to every game, so all of them are
    checked. Capacity is a hard limit for the organizer too, so this
    is a refusal, not a warning. Counts are read once for the whole
    season rather than per game; the formula is _expected_on_court's.
    """
    games = (
        db.query(GameRow)
        .filter(GameRow.season_id == season.id, GameRow.status == GameStatus.SCHEDULED)
        .order_by(GameRow.date)
        .with_for_update()
        .all()
    )
    if not games:
        return []

    if member_count is None:
        member_count = (
            db.query(func.count(SeasonMemberRow.player_id))
            .filter(SeasonMemberRow.season_id == season.id)
            .scalar()
            or 0
        )
    game_ids = [game.id for game in games]
    absence_counts = (
        db.query(AbsenceRow.game_id, func.count(AbsenceRow.id))
        .join(
            SeasonMemberRow,
            and_(
                SeasonMemberRow.player_id == AbsenceRow.player_id,
                SeasonMemberRow.season_id == season.id,
            ),
        )
        .filter(
            AbsenceRow.game_id.in_(game_ids),
            AbsenceRow.cancelled_at.is_(None),
        )
        .group_by(AbsenceRow.game_id)
        .all()
    )
    absences_by_game: dict[int, int] = {
        int(game_id): int(count) for game_id, count in absence_counts
    }
    drop_in_counts = (
        db.query(DropInRow.game_id, func.count(DropInRow.id))
        .filter(DropInRow.game_id.in_(game_ids), DropInRow.playing())
        .group_by(DropInRow.game_id)
        .all()
    )
    drop_ins_by_game: dict[int, int] = {
        int(game_id): int(count) for game_id, count in drop_in_counts
    }
    return [
        game
        for game in games
        if member_count
        - int(absences_by_game.get(game.id, 0))
        + int(drop_ins_by_game.get(game.id, 0))
        >= season.capacity
    ]


def _deadline_at(game: GameRow, season: SeasonRow) -> datetime:
    """When members stop being able to change this game: the season's
    deadline in hours before the start time (the game's own, else the
    season's, else midnight, so a game with no time closes early
    rather than late).
    """
    return change_deadline(
        game.date,
        game.start_time or season.game_start_time,
        season.change_deadline_hours,
    )


def _within_change_deadline(game: GameRow, season: SeasonRow) -> bool:
    """Whether members may still change attendance for this game."""
    return _now_in_taiwan() < _deadline_at(game, season)


def _require_season_open(season: SeasonRow) -> None:
    """Refuses any change to a settled season.

    Kept separate from _require_within_change_deadline because the
    organizer is exempt from the deadline but not from this.
    """
    if season.settled_at is not None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Season is already settled",
        )


def _require_within_change_deadline(
    db: Session, game: GameRow, season: SeasonRow, current_player: PlayerRow
) -> None:
    """Refuses a change after the deadline, except from the organizer,
    who has to be able to record what actually happened on the night.
    """
    if _within_change_deadline(game, season):
        return
    if _runs_club(db, season.club_id, current_player):
        return
    raise HTTPException(
        status.HTTP_400_BAD_REQUEST,
        "Past this season's change deadline for this game",
    )


def _is_absence_covered(db: Session, absence_row: AbsenceRow) -> bool:
    """Whether a drop-in (a named substitute or a FIFO match) currently
    covers this absence, by the same rule settlement uses for refunds.
    """
    game_row = db.get(GameRow, absence_row.game_id)
    assert game_row is not None
    game = game_from_row(game_row)

    absence_rows = db.query(AbsenceRow).filter(AbsenceRow.game_id == game_row.id).all()
    # Never the queue: somebody waiting covers nobody.
    drop_in_rows = (
        db.query(DropInRow)
        .filter(DropInRow.game_id == game_row.id, DropInRow.status == PLAYING)
        .all()
    )
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


def _back_to_the_queue(db: Session, drop_in: DropInRow, season: SeasonRow) -> None:
    """Moves somebody off the court and back into the queue at the place
    they held (when they first queued, or signed up if they never
    did). Their fee comes off and any 代打 arrangement is dropped. The
    row changes status; nothing is copied.
    """
    _record_drop_in_charge(db, drop_in, season, reverse=True)
    drop_in.queued_at = drop_in.queued_at or drop_in.signed_up_at
    drop_in.status = QUEUED
    drop_in.covers_absence_id = None
    db.flush()


def _off_the_court(db: Session, drop_in: DropInRow, season: SeasonRow) -> None:
    """Takes a place off somebody, done by anyone but themselves.

    Somebody who came from the queue goes back to it at their old
    position. Somebody who never queued has no place to return to, so
    their signup is cancelled instead of putting them in a queue they
    never joined.
    """
    if drop_in.queued_at is not None:
        _back_to_the_queue(db, drop_in, season)
        return
    drop_in.cancelled_at = _now()
    _record_drop_in_charge(db, drop_in, season, reverse=True)
    db.flush()


def _displace_latest_drop_in(
    db: Session, game: GameRow, season: SeasonRow
) -> int | None:
    """Moves the latest confirmed drop-in back to this game's queue, to
    make room for a fixed member being added to the roster.

    Last in, first out; named substitutes are included, because fixed
    membership outranks every drop-in. Returns the displaced player's
    id, or None if there was no drop-in to move.
    """
    displaced = (
        db.query(DropInRow)
        .filter(DropInRow.game_id == game.id, DropInRow.playing())
        .order_by(DropInRow.signed_up_at.desc(), DropInRow.id.desc())
        .with_for_update()
        .first()
    )
    if displaced is None:
        return None

    _back_to_the_queue(db, displaced, season)
    return int(displaced.player_id)


def _release_whoever_is_covering(
    db: Session, absence: AbsenceRow, season: SeasonRow
) -> int | None:
    """Puts the stand-in back in the queue when the absent member
    returns.

    Their named 代打 if they have one, otherwise the drop-in the FIFO
    match landed on. Somebody else's named substitute is never
    touched. Returns the released player's id so the screen can say
    who it was.
    """
    arranged = (
        db.query(DropInRow)
        .filter(DropInRow.covers_absence_id == absence.id, DropInRow.playing())
        .first()
    )
    releasing = arranged
    if releasing is None:
        if not _is_absence_covered(db, absence):
            return None
        # The FIFO match, in the same order as settlement.covered_absences.
        releasing = (
            db.query(DropInRow)
            .filter(
                DropInRow.game_id == absence.game_id,
                DropInRow.playing(),
                DropInRow.covers_absence_id.is_(None),
            )
            .order_by(DropInRow.signed_up_at.desc())
            .first()
        )
    if releasing is None:
        return None

    released_player_id = int(releasing.player_id)
    # The place they held, not the moment they were named a 代打.
    _back_to_the_queue(db, releasing, season)
    return released_player_id


def _make_room_for_substitute(
    db: Session, game: GameRow, season: SeasonRow
) -> int | None:
    """Frees the slot a named substitute is about to take, and returns
    whose it was.

    The queue may already have filled the slot the absence opened. The
    most recent signup nobody arranged goes back to the queue at their
    original place, with their fee reversed; another member's named
    substitute is never bumped. Returns None if there was already
    room, and refuses if everyone on court was personally arranged.
    """
    if _has_open_slot(db, game, season):
        return None

    displaced = (
        db.query(DropInRow)
        .filter(
            DropInRow.game_id == game.id,
            DropInRow.playing(),
            DropInRow.covers_absence_id.is_(None),
        )
        .order_by(DropInRow.signed_up_at.desc())
        .first()
    )
    if displaced is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"This game is full ({season.capacity}) and everyone in it was "
            "personally arranged — cancel one of them first.",
        )

    _back_to_the_queue(db, displaced, season)
    return int(displaced.player_id)


def _require_may_cancel_drop_in(
    db: Session, drop_in: DropInRow, season: SeasonRow, current_player: PlayerRow
) -> None:
    """Who may take a confirmed signup off the list: the player, whoever
    signed them up, the member whose absence they were arranged to
    cover, or the organizer. Anybody else gets 403.
    """
    if drop_in.player_id == current_player.id:
        return
    if drop_in.brought_by_player_id == current_player.id:
        return
    if drop_in.covers_absence_id is not None:
        absence = db.get(AbsenceRow, drop_in.covers_absence_id)
        if absence is not None and absence.player_id == current_player.id:
            return
    _require_organizer(db, season.club_id, current_player)


def _promote_entry(db: Session, entry: DropInRow) -> DropInRow:
    """Turns one queued person into a confirmed drop-in, charged exactly
    as a direct signup. Shared by automatic and manual promotion.
    """
    # Their signup time becomes when they joined the queue, which FIFO
    # coverage orders by. queued_at stays, so the place can be given back if
    # the slot is taken away again.
    assert entry.queued_at is not None
    entry.signed_up_at = entry.queued_at
    entry.status = PLAYING

    game = db.get(GameRow, entry.game_id)
    assert game is not None
    season_row = db.get(SeasonRow, game.season_id)
    assert season_row is not None
    _record_drop_in_charge(db, entry, season_row, reverse=False)

    return entry


def _promote_from_waitlist(db: Session, game_id: int) -> int | None:
    """Promotes the earliest-queued person for this game. Returns their
    player id, or None if nobody was waiting.
    """
    entry = (
        db.query(DropInRow)
        .filter(DropInRow.game_id == game_id, DropInRow.queued())
        .order_by(DropInRow.queued_at, DropInRow.id)
        .first()
    )
    if entry is None:
        return None
    return _promote_entry(db, entry).player_id


def _offer_freed_slots_to_the_queue(
    db: Session, season: SeasonRow
) -> list[tuple[int, int]]:
    """Offers the queue the places freed at every future game when
    somebody is taken off the roster, earliest queued first.

    Whoever is promoted joins that one game as a drop-in; they do not
    take the fixed place. Games already played are skipped, so nobody
    is charged for a night they did not attend. Returns (game_id,
    player_id) pairs, so each person can be told which night is
    theirs.
    """
    promoted: list[tuple[int, int]] = []
    today = _today_in_taiwan()
    # Locked like _get_game_or_404, in date order so concurrent removals
    # take the games in the same sequence instead of deadlocking.
    queued_game_ids = db.query(DropInRow.game_id).filter(DropInRow.queued())
    games = (
        db.query(GameRow)
        .filter(
            GameRow.season_id == season.id,
            GameRow.status == GameStatus.SCHEDULED,
            GameRow.date >= today,
            GameRow.id.in_(queued_game_ids),
        )
        .order_by(GameRow.date)
        .with_for_update()
        .all()
    )
    for game in games:
        # Keep offering until the court is full or the queue is empty.
        while _has_open_slot(db, game, season):
            player_id = _promote_from_waitlist(db, game.id)
            if player_id is None:
                break
            db.flush()
            promoted.append((game.id, player_id))
    return promoted


def _absorb_drop_ins_into_membership(
    db: Session, season: SeasonRow, player_id: int
) -> None:
    """Cancels the drop-in signups of somebody just made a fixed member,
    and refunds their drop-in fees for the season, since their season
    fee now covers every game.

    No slot opens (they are still expected), so nobody is promoted.
    """
    game_ids = [
        row.id
        for row in db.query(GameRow)
        .filter(GameRow.season_id == season.id)
        .order_by(GameRow.date)
        .all()
    ]
    if not game_ids:
        return

    now = _now()
    drop_ins = (
        db.query(DropInRow)
        .filter(
            DropInRow.player_id == player_id,
            DropInRow.game_id.in_(game_ids),
            DropInRow.playing(),
        )
        .all()
    )
    for drop_in in drop_ins:
        drop_in.cancelled_at = now
        # Marked so a later removal can restore exactly these; see
        # _restore_absorbed_drop_ins.
        drop_in.absorbed_at = now

    # Refund whatever drop-in fees they were charged this season, rather
    # than a recomputed share, so the total comes to exactly zero.
    charged = sum(
        (
            amount
            for (amount,) in db.query(LedgerEntryRow.amount).filter(
                LedgerEntryRow.player_id == player_id,
                LedgerEntryRow.season_id == season.id,
                LedgerEntryRow.entry_type == EntryType.DROP_IN_FEE_CHARGED,
            )
        ),
        Decimal("0"),
    )
    if charged != 0:
        db.add(
            LedgerEntryRow(
                player_id=player_id,
                club_id=season.club_id,
                entry_type=EntryType.DROP_IN_FEE_CHARGED,
                amount=-charged,
                recorded_at=now,
                season_id=season.id,
                note="Drop-in fees refunded — now a fixed member",
            )
        )

    # Queue places are cancelled without the mark: they carry no money or
    # attendance, so they are not restored later.
    for queued in db.query(DropInRow).filter(
        DropInRow.player_id == player_id,
        DropInRow.game_id.in_(game_ids),
        DropInRow.queued(),
    ):
        queued.cancelled_at = now
    db.flush()


def _close_absences_of_former_member(
    db: Session, season: SeasonRow, player_id: int
) -> set[int]:
    """Retires the leave of somebody taken off the roster.

    Whoever stood in for them keeps the slot, as an ordinary drop-in
    rather than the 代打 of somebody no longer on the season. Marked
    `retired_at` so re-adding the same person can restore their leave.
    Returns the games they were away from.
    """
    game_ids = [
        row.id for row in db.query(GameRow).filter(GameRow.season_id == season.id)
    ]
    if not game_ids:
        return set()

    absences = (
        db.query(AbsenceRow)
        .filter(
            AbsenceRow.player_id == player_id,
            AbsenceRow.game_id.in_(game_ids),
            AbsenceRow.cancelled_at.is_(None),
        )
        .all()
    )
    if not absences:
        return set()

    now = _now()
    for absence in absences:
        absence.cancelled_at = now
        absence.retired_at = now
        db.query(DropInRow).filter(DropInRow.covers_absence_id == absence.id).update(
            {DropInRow.covers_absence_id: None}, synchronize_session=False
        )
    db.flush()
    return {int(absence.game_id) for absence in absences}


def _retired_absences(
    db: Session, season: SeasonRow, player_id: int
) -> list[AbsenceRow]:
    """The leave this player gets back by rejoining the season's roster:
    the absences a removal closed, never ones they cancelled
    themselves.
    """
    game_ids = [
        row.id for row in db.query(GameRow).filter(GameRow.season_id == season.id)
    ]
    if not game_ids:
        return []
    return (
        db.query(AbsenceRow)
        .filter(
            AbsenceRow.player_id == player_id,
            AbsenceRow.game_id.in_(game_ids),
            AbsenceRow.retired_at.is_not(None),
        )
        .all()
    )


def _retired_absence_game_ids(
    db: Session, season: SeasonRow, player_id: int
) -> set[int]:
    return {int(a.game_id) for a in _retired_absences(db, season, player_id)}


def _restore_retired_absences(db: Session, season: SeasonRow, player_id: int) -> None:
    """Puts back the leave a removal closed when the same person rejoins
    the roster.

    The 代打 link is not restored. Leave is not put back on a night they
    have since signed up for, because that signup is the later word.
    """
    game_ids = [
        row.id for row in db.query(GameRow).filter(GameRow.season_id == season.id)
    ]
    signed_up_for = {
        int(game_id)
        for (game_id,) in db.query(DropInRow.game_id).filter(
            DropInRow.player_id == player_id,
            DropInRow.game_id.in_(game_ids),
            DropInRow.playing(),
        )
    }
    absences = _retired_absences(db, season, player_id)
    for absence in absences:
        # The mark comes off whether or not the leave comes back.
        absence.retired_at = None
        if int(absence.game_id) in signed_up_for:
            continue
        absence.cancelled_at = None
    if absences:
        db.flush()


def _restore_absorbed_drop_ins(
    db: Session, season: SeasonRow, player_id: int, away_from: set[int] | None = None
) -> None:
    """Undoes _absorb_drop_ins_into_membership when the player leaves the
    roster again: puts back the signups cancelled only because they
    joined it, charged at the current share.

    Only rows marked `absorbed_at` are touched. Games in `away_from`
    (where they had taken leave as a member) stay cancelled. Capacity
    is not rechecked: this restores nights that were already theirs.
    """
    game_ids = [
        row.id for row in db.query(GameRow).filter(GameRow.season_id == season.id).all()
    ]
    if not game_ids:
        return

    absorbed = (
        db.query(DropInRow)
        .filter(
            DropInRow.player_id == player_id,
            DropInRow.game_id.in_(game_ids),
            DropInRow.absorbed_at.is_not(None),
        )
        .order_by(DropInRow.signed_up_at)
        .all()
    )
    skip = set(away_from or set())
    # One live row per person per game (uq_drop_ins_active_player_game),
    # queued included, so skip games where they already have one.
    taken = {
        int(game_id)
        for (game_id,) in db.query(DropInRow.game_id).filter(
            DropInRow.player_id == player_id,
            DropInRow.game_id.in_(game_ids),
            DropInRow.cancelled_at.is_(None),
        )
    }
    restored = False
    for drop_in in absorbed:
        # Cleared either way, so a later removal cannot restore a row this
        # one decided against.
        drop_in.absorbed_at = None
        game_id = int(drop_in.game_id)
        if game_id in skip or game_id in taken:
            continue
        drop_in.cancelled_at = None
        taken.add(game_id)
        restored = True
        _record_drop_in_charge(db, drop_in, season, reverse=False)
    if absorbed or restored:
        db.flush()


def _delete_season_rows(db: Session, season: SeasonRow) -> None:
    """Deletes a season and everything under it, children first so no
    foreign key dangles.
    """
    game_ids = [
        row.id
        for row in db.query(GameRow)
        .filter(GameRow.season_id == season.id)
        .order_by(GameRow.date)
        .all()
    ]
    if game_ids:
        absence_ids = [
            row.id
            for row in db.query(AbsenceRow)
            .filter(AbsenceRow.game_id.in_(game_ids))
            .all()
        ]
        db.query(DropInRow).filter(DropInRow.game_id.in_(game_ids)).delete(
            synchronize_session=False
        )
        if absence_ids:
            db.query(AbsenceRow).filter(AbsenceRow.id.in_(absence_ids)).delete(
                synchronize_session=False
            )
    db.query(LedgerEntryRow).filter(LedgerEntryRow.season_id == season.id).delete(
        synchronize_session=False
    )
    db.query(SeasonMemberRow).filter(SeasonMemberRow.season_id == season.id).delete(
        synchronize_session=False
    )
    db.query(GameRow).filter(GameRow.season_id == season.id).delete(
        synchronize_session=False
    )
    db.delete(season)
