"""The rules about who is on court, and the machinery that moves
them.

The top helper layer: capacity, deadlines, the queue, substitutes, and
the absorb/restore pairs that make adding and removing a fixed member
undoable. Calls down into _money to record what those movements cost.
"""

from datetime import timedelta
from decimal import Decimal

from fastapi import (
    HTTPException,
    status,
)
from sqlalchemy import and_
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
    _require_organizer,
    _today_in_taiwan,
)
from volleyflow.db.models import (
    AbsenceRow,
    ClubMemberRow,
    DropInRow,
    GameRow,
    LedgerEntryRow,
    PlayerRow,
    SeasonMemberRow,
    SeasonRow,
    WaitlistEntryRow,
)
from volleyflow.ledger import EntryType
from volleyflow.schedule import GameStatus
from volleyflow.settlement import (
    covered_absences,
)


def _reject_if_already_playing(
    db: Session, game: GameRow, season: SeasonRow, player: PlayerRow
) -> None:
    """The three ways a signup would double-book someone for one game.

    Shared by the single and batch signup routes so the two can't drift
    apart on what counts as a duplicate — the batch route in particular
    must reject the whole group rather than let one bad entry through.
    """
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
            f"{player.name} is already signed up for this game",
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
            f"{player.name} is already on the waitlist for this game",
        )

    # A fixed member is expected at every game already (CLAUDE.md 2.3);
    # signing up as a drop-in on top of that would charge them the
    # per-game share a second time, on top of the season fee that
    # already covers this game, and count them twice against capacity.
    # Easy to do by accident: someone new joins the club, sees a signup
    # button, taps it, and only afterwards gets added to the roster.
    fixed_member = db.get(
        SeasonMemberRow, {"season_id": season.id, "player_id": player.id}
    )
    if fixed_member is not None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"{player.name} is a fixed member of this season — no need to sign up",
        )


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


def _expected_on_court(db: Session, game: GameRow, season: SeasonRow) -> int:
    """How many people this game expects: fixed members minus this game's
    absences, plus active drop-ins. Members who haven't taken leave count
    as attending by default — see CLAUDE.md 2.3, "members are expected by
    default."

    Independent of `season.capacity` on purpose: `update_season` needs
    this number to check a *proposed* capacity before committing to it,
    not the one already on the row.
    """
    member_count = (
        db.query(SeasonMemberRow).filter(SeasonMemberRow.season_id == season.id).count()
    )
    # cancelled_at matters: a member who took leave and then cancelled it
    # is expected again. Counting their cancelled absence made `expected`
    # too low and the game look emptier than it is, so capacity admitted
    # one drop-in too many for every absence that had ever been undone.
    #
    # The join is the same mistake in its other form. Taking somebody off
    # the roster deliberately leaves their absences behind (see
    # remove_member — settlement only ever walks the current member list,
    # so they are harmless there), but this sum is not settlement: an
    # absence with nobody on the roster behind it was subtracting a head
    # that `member_count` had already stopped counting, so every removal
    # made the game look one seat emptier forever. A random sweep put
    # seven people on a court for six that way.
    #
    # remove_member now closes those absences at the source, so new ones
    # can't appear — but rows written before it did are still in the
    # database, and this is what keeps them from mispricing a live
    # season. It is also just the right question to ask: how many
    # *members* are away.
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
        .filter(DropInRow.game_id == game.id, DropInRow.cancelled_at.is_(None))
        .count()
    )
    return (member_count - absences) + active_drop_ins


def _has_open_slot(db: Session, game: GameRow, season: SeasonRow) -> bool:
    return _expected_on_court(db, game, season) < season.capacity


def _is_signed_up(db: Session, game: GameRow, player_id: int) -> bool:
    """Whether this player already holds a confirmed slot at this game."""
    return (
        db.query(DropInRow)
        .filter(
            DropInRow.game_id == game.id,
            DropInRow.player_id == player_id,
            DropInRow.cancelled_at.is_(None),
        )
        .first()
    ) is not None


def _games_with_no_room(db: Session, season: SeasonRow) -> list[GameRow]:
    """The games where one more expected body wouldn't fit.

    Adding a fixed member adds them to *every* game in the season, so the
    question "is there room" has to be asked of all of them at once. A
    random sweep (tests/api/test_fuzz.py) put seven people on a court for
    six that way: the organizer added a member while a game was already
    full of drop-ins, and nothing checked. CLAUDE.md 2.3 makes capacity a
    hard limit that the organizer does not get to exceed either, so this
    is a refusal rather than a warning.
    """
    games = (
        db.query(GameRow)
        .filter(GameRow.season_id == season.id, GameRow.status == GameStatus.SCHEDULED)
        .order_by(GameRow.date)
        .all()
    )
    return [game for game in games if not _has_open_slot(db, game, season)]


def _within_change_deadline(game: GameRow, season: SeasonRow) -> bool:
    """Whether absence/signup changes (and cancelling either) are still
    allowed for this game. None means no deadline — CLAUDE.md 2.3's
    stated default; otherwise a change must land at least this many
    days before the game.
    """
    if season.change_deadline_days is None:
        return True
    return _today_in_taiwan() + timedelta(days=season.change_deadline_days) <= game.date


def _require_season_open(season: SeasonRow) -> None:
    """Refuses any change to a season whose books are closed.

    `CLAUDE.md` 2.4: settlement "computes each member's absence refund and
    locks the season". The roster endpoints had always honoured that;
    attendance did not, and the gap was not theoretical — measured on
    2026-09-12, after settling a season you could still sign somebody up,
    which wrote them a real charge onto closed books, and still record a
    member's leave, which earned a refund that could never be paid
    because a season cannot be settled twice.

    Deliberately not folded into _require_within_change_deadline below,
    though every caller wants both: that rule exempts the organizer, and
    this one must not. The organizer is exactly the person with the
    buttons to do this, and "the books are closed" is not a rule they are
    above — it is the one they most need held to.
    """
    if season.settled_at is not None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Season is already settled",
        )


def _require_within_change_deadline(
    db: Session, game: GameRow, season: SeasonRow, current_player: PlayerRow
) -> None:
    """The deadline exists so a roster stops shifting under the organizer
    at the last minute — so it doesn't apply to the organizer. They're
    the one absorbing whatever happens on the day: someone drops out an
    hour before, a replacement turns up, a court gets cancelled. Blocking
    them from recording that doesn't make the roster more accurate, it
    just makes the records wrong.
    """
    if _within_change_deadline(game, season):
        return
    membership = db.get(
        ClubMemberRow, {"club_id": season.club_id, "player_id": current_player.id}
    )
    if membership is not None and membership.role == "organizer":
        return
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


def _give_back_queue_place(db: Session, drop_in: DropInRow) -> None:
    """Returns a signup's queue place, when it had one.

    A place in the queue is given up, not lost: somebody who left it to
    take a slot is owed it back at the position they held, if the slot is
    taken off them by anybody but themselves. Somebody who was never
    waiting — typed in by name, or signed straight into an open slot —
    has no place to give back, and must not be put into a queue they
    never joined, or they reappear in the next open slot.

    One function because the rule has to hold in every path that takes a
    slot away: cancelling a substitute, replacing one with somebody else,
    and the organizer clearing a row. Replacing one was written without
    it and silently deleted the person being replaced — reported from
    real use on 2026-09-11 as "候補會不見".
    """
    if drop_in.from_waitlist_at is None:
        return
    db.add(
        WaitlistEntryRow(
            player_id=drop_in.player_id,
            game_id=drop_in.game_id,
            queued_at=drop_in.from_waitlist_at,
        )
    )
    db.flush()


def _release_whoever_is_covering(
    db: Session, absence: AbsenceRow, season: SeasonRow
) -> int | None:
    """Puts the stand-in back in the queue when the member returns.

    The named 代打 first, if there is one — the member arranged them, so
    the member may un-arrange them. Otherwise whichever 臨打 the FIFO
    match had landed on, which is the person the slot actually went to.
    Somebody else's named substitute is never touched: that slot belongs
    to a different absence.

    They go back at the time they originally queued, so they keep their
    place, and the fee comes off. Returns whose it was, so the screen can
    say — a player silently vanishing off a roster is worse than the
    swap itself.
    """
    arranged = (
        db.query(DropInRow)
        .filter(
            DropInRow.covers_absence_id == absence.id,
            DropInRow.cancelled_at.is_(None),
        )
        .first()
    )
    releasing = arranged
    if releasing is None:
        if not _is_absence_covered(db, absence):
            return None
        # The FIFO match: the earliest unclaimed signup is the one this
        # absence's refund is paying for, so it is the one that steps
        # back out. Same ordering as settlement.covered_absences.
        releasing = (
            db.query(DropInRow)
            .filter(
                DropInRow.game_id == absence.game_id,
                DropInRow.cancelled_at.is_(None),
                DropInRow.covers_absence_id.is_(None),
            )
            .order_by(DropInRow.signed_up_at.desc())
            .first()
        )
    if releasing is None:
        return None

    released_player_id = int(releasing.player_id)
    releasing.cancelled_at = _now()
    _record_drop_in_charge(db, releasing, season, reverse=True)
    db.add(
        WaitlistEntryRow(
            player_id=releasing.player_id,
            game_id=absence.game_id,
            queued_at=releasing.signed_up_at,
        )
    )
    db.flush()
    return released_player_id


def _make_room_for_substitute(
    db: Session, game: GameRow, season: SeasonRow
) -> int | None:
    """Frees the slot a named substitute is about to take, and says whose
    it was.

    Recording an absence offers the empty slot to the waitlist straight
    away (CLAUDE.md 2.3), so by the time the member names the person they
    actually wanted, a queued stranger is usually already standing in it.
    Adding the substitute on top of that put a nineteenth player in an
    eighteen-player game — reported from real use on 2026-09-10, and the
    reason this exists.

    The person bumped is the most recent signup nobody personally
    arranged. Somebody else's named substitute is never touched: they
    were chosen the same way this one is. Being bumped puts them back in
    the queue at the time they originally signed up, so they keep their
    place ahead of anyone who joined it later, and their fee comes off.

    Returns None when there was already room, and raises when the only
    people here were personally arranged — then there is no fair pick and
    the organizer has to decide.
    """
    if _has_open_slot(db, game, season):
        return None

    displaced = (
        db.query(DropInRow)
        .filter(
            DropInRow.game_id == game.id,
            DropInRow.cancelled_at.is_(None),
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

    displaced.cancelled_at = _now()
    _record_drop_in_charge(db, displaced, season, reverse=True)
    db.add(
        WaitlistEntryRow(
            player_id=displaced.player_id,
            game_id=game.id,
            queued_at=displaced.signed_up_at,
        )
    )
    db.flush()
    return int(displaced.player_id)


def _require_may_cancel_drop_in(
    db: Session, drop_in: DropInRow, season: SeasonRow, current_player: PlayerRow
) -> None:
    """Who may take a confirmed signup back off the list.

    Three people besides the organizer, and a check on the player alone
    missed two of them:

    * the player themselves;
    * whoever brought them — a guest has no account and never will, so
      the member who signed them up is the only one who can undo it;
    * the member whose absence they were arranged to cover — the screen
      offers that member 取消代打, and refusing it here made the button
      fail with a 403 for everyone except the organizer.

    Anybody else gets 403, including a member acting on a stranger's
    signup and a member acting on a substitute somebody else arranged.
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


def _promote_entry(db: Session, entry: WaitlistEntryRow) -> DropInRow:
    """Turn one queued person into a confirmed drop-in, charged exactly
    as a direct signup would be.

    Shared by the automatic promotion below and the organizer's manual
    one, so "what promoting somebody means" — including the ledger entry
    — is written once and cannot drift between the two.
    """
    game_id = entry.game_id
    # signed_up_at is when they joined the queue, not when the slot
    # happened to open. It is what FIFO absence coverage orders by, and
    # it is what a later bump reads to put them back in the queue where
    # they were — stamping "now" instead sent somebody who had waited
    # longest to the back of the line.
    drop_in = DropInRow(
        player_id=entry.player_id,
        game_id=game_id,
        signed_up_at=entry.queued_at,
        # Where they were in the queue, kept so the place can be given
        # back if this slot is taken off them again.
        from_waitlist_at=entry.queued_at,
    )
    db.add(drop_in)
    db.delete(entry)

    game = db.get(GameRow, game_id)
    assert game is not None
    season_row = db.get(SeasonRow, game.season_id)
    assert season_row is not None
    _record_drop_in_charge(db, drop_in, season_row, reverse=False)

    return drop_in


def _promote_from_waitlist(db: Session, game_id: int) -> int | None:
    """Pull the earliest-queued waitlist entry into a confirmed drop-in.

    Returns the promoted player's id, or None if nobody was waiting. See
    CLAUDE.md 2.3: "a member records an absence -> the waitlist is offered
    the slot in order." Order is the rule here and stays the rule; the
    organizer overrides it explicitly through promote_from_waitlist.
    """
    entry = (
        db.query(WaitlistEntryRow)
        .filter(WaitlistEntryRow.game_id == game_id)
        .order_by(WaitlistEntryRow.queued_at)
        .first()
    )
    if entry is None:
        return None
    return _promote_entry(db, entry).player_id


def _offer_freed_slots_to_the_queue(db: Session, season: SeasonRow) -> list[int]:
    """Taking somebody off the roster frees their place at every game they
    were expected at, so the queue is offered those places — earliest
    queued first, one person per place, exactly as an absence does.

    Asked for on 2026-09-12, after the removal fix turned up a game
    sitting at 2 on a court for 3 with somebody still waiting in the
    queue for it. `CLAUDE.md` 2.3 lists an absence and a cancelled signup
    as the moments a slot is offered on; a removal frees a slot just as
    squarely and was simply not on the list.

    Whoever comes off the queue joins that one game as a drop-in — the
    organizer's words: 「不代表他會占掉原來的固定名額，他只是來候補那一
    場而已」. That is already what promotion means everywhere else
    (_promote_entry writes a DropInRow and charges one game's share), so
    nothing here has to make it true; it is worth writing down only
    because "somebody left the roster, so somebody else joins it" would
    be the wrong reading.

    Future games only. A removal frees the slot at every game in the
    season including ones already played, and promoting somebody into
    last month's game would put them on a roster they never stood on and
    charge them for the night.
    """
    promoted: list[int] = []
    today = _today_in_taiwan()
    games = (
        db.query(GameRow)
        .filter(
            GameRow.season_id == season.id,
            GameRow.status == GameStatus.SCHEDULED,
            GameRow.date >= today,
        )
        .order_by(GameRow.date)
        .all()
    )
    for game in games:
        # A game can free more than one place if the roster shrank by
        # more than one person before this ran, so keep offering until
        # the court is full or the queue is empty.
        while _has_open_slot(db, game, season):
            player_id = _promote_from_waitlist(db, game.id)
            if player_id is None:
                break
            db.flush()
            promoted.append(player_id)
    return promoted


def _absorb_drop_ins_into_membership(
    db: Session, season: SeasonRow, player_id: int
) -> None:
    """Someone who signed up as a drop-in and is now being made a fixed
    member: cancel those signups and hand the fees back.

    This is the ordinary sequence for a new person, not an edge case —
    they join the club, see a signup button, tap it, and the organizer
    adds them to the roster afterwards. Left alone they'd be charged the
    per-game share twice for the same game (once as a drop-in, once
    inside their season fee) and counted twice against capacity.

    No waitlist promotion here, deliberately: they were already counted
    as attending and still are, just as a member now, so no slot opened.
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
            DropInRow.cancelled_at.is_(None),
        )
        .all()
    )
    for drop_in in drop_ins:
        drop_in.cancelled_at = now
        # Marked, so removing them from the roster later can tell these
        # apart from signups the player cancelled themselves and put
        # exactly these back — see _restore_absorbed_drop_ins.
        drop_in.absorbed_at = now

    # Reverse to a target of nothing owed, rather than recomputing a
    # share and subtracting that: the share when they signed up was
    # divided among a smaller roster than the one they've just joined,
    # so a freshly computed refund would be smaller than what was
    # actually taken and quietly leave them short. Their season fee now
    # covers every game, so their drop-in fees for this season must come
    # to exactly zero, whatever they were charged along the way.
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

    # Waiting for a slot is equally moot once they're on the roster.
    # Deleted rather than marked, and deliberately not restored if they
    # later come off the roster: a queue place carries no money and no
    # attendance, and putting somebody back into a queue that has moved
    # on since would be a guess. A drop-in is the opposite — a night
    # somebody really played and really owes for — which is why that one
    # is marked and does come back.
    db.query(WaitlistEntryRow).filter(
        WaitlistEntryRow.player_id == player_id,
        WaitlistEntryRow.game_id.in_(game_ids),
    ).delete(synchronize_session=False)
    db.flush()


def _close_absences_of_former_member(
    db: Session, season: SeasonRow, player_id: int
) -> set[int]:
    """Retires the leave records of somebody taken off the roster.

    Whoever was standing in for them keeps the slot, and deliberately so:
    removing an absent member drops both a name from the roster and an
    absence from the count, which leaves expected attendance exactly
    where it was. Releasing the substitute as well would open a slot that
    nothing has freed.

    The arrangement is dropped though — `covers_absence_id` is cleared —
    so they become an ordinary drop-in. They stay on the list, but they
    are no longer "X's 代打" for an X who has left the season, which is
    both untrue and the sort of thing the game sheet would print.

    Marked `retired_at`, not merely cancelled, so putting the same person
    back on the roster can put their leave back with them. Without that
    mark the two look identical and a removal could not be undone: the
    substitute keeps the slot, so the game stays at capacity while the
    roster drops by one, and re-adding the person is refused for having
    no room on a court they were never going to stand on.

    Returns the games they were away from, because the absorbed signups
    restored next must not put them back into one of those.
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
    """The leave records this player would get back by rejoining this
    season's roster — the ones a removal closed, never the ones they
    cancelled themselves.
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
    """Puts back the leave a removal closed, when the same person is put
    back on the same season's roster.

    The mirror of _restore_absorbed_drop_ins, and for the same reason:
    taking somebody off the roster must be undoable. They said they were
    away on those nights and somebody is standing in for them; coming
    back to the roster does not make them available on nights they had
    already said they would miss.

    The 代打 arrangement is deliberately not re-made — `covers_absence_id`
    stays cleared. Who personally arranged whom is a relationship between
    two people (CLAUDE.md 2.3) and it was genuinely dropped when the
    member left; the FIFO rule that decides which absence gets refunded
    is billing, works off the counts, and needs no such link.
    """
    absences = _retired_absences(db, season, player_id)
    for absence in absences:
        absence.cancelled_at = None
        absence.retired_at = None
    if absences:
        db.flush()


def _restore_absorbed_drop_ins(
    db: Session, season: SeasonRow, player_id: int, away_from: set[int] | None = None
) -> None:
    """The mirror of _absorb_drop_ins_into_membership: put back the
    signups that were cancelled only because this player joined the
    fixed roster, now that they have left it again.

    Only rows carrying `absorbed_at` are touched, so a signup the player
    cancelled themselves stays cancelled — they are indistinguishable in
    `cancelled_at`, and resurrecting one would put somebody on a roster
    they had deliberately left.

    `away_from` names the games they had taken leave from as a member,
    and those signups stay cancelled too. The absence is the later and
    more specific statement: somebody who signed up as a guest, was put
    on the roster, and then said they can't make that night is not coming
    to it, whatever happens to their membership afterwards. Restoring it
    anyway put a seventh person on a six-person court — found by a random
    sweep, since it needs a signup, a promotion to the roster, an absence
    and a removal, in that order, to show up at all.

    Charged at the current per-game share rather than at whatever was
    refunded: that is what a drop-in for this game costs now, and with
    the roster back to its previous size it is normally the same figure
    anyway. Capacity is not rechecked — this is restoring a night that
    already happened, not admitting somebody new.
    """
    game_ids = [
        row.id
        for row in db.query(GameRow).filter(GameRow.season_id == season.id).all()
        if row.id not in (away_from or set())
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
        .all()
    )
    for drop_in in absorbed:
        drop_in.cancelled_at = None
        drop_in.absorbed_at = None
        _record_drop_in_charge(db, drop_in, season, reverse=False)
    if absorbed:
        db.flush()


def _delete_season_rows(db: Session, season: SeasonRow) -> None:
    """Removes a season and everything hanging off it, children first so
    no foreign key is ever left dangling."""
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
        db.query(WaitlistEntryRow).filter(
            WaitlistEntryRow.game_id.in_(game_ids)
        ).delete(synchronize_session=False)
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
