"""API routes."""

import base64
import binascii
import os
import re
import secrets
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal
from typing import cast

from fastapi import (
    APIRouter,
    Depends,
    Header,
    HTTPException,
    Request,
    Response,
    status,
)
from sqlalchemy import case, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, aliased

from volleyflow.api.auth import verify_id_token
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
    AirConditioningUpdate,
    ClubCreate,
    ClubMemberOut,
    ClubOut,
    DropInBatchCreate,
    DropInBatchEntry,
    DropInBatchOut,
    DropInCancelOut,
    DropInCreate,
    DropInDetailOut,
    DropInOut,
    DropInSummary,
    GameCancel,
    GameDetailOut,
    GameOut,
    Gender,
    GenderUpdate,
    LedgerEntryOut,
    MemberAdd,
    MemberOut,
    MemberSettlementOut,
    MembershipIntent,
    MyClubOut,
    NameUpdate,
    PaymentCreate,
    PlayerBalanceOut,
    PlayerIdentify,
    PlayerIdentifyOut,
    PlayerLedgerOut,
    PlayerLink,
    ProblemReport,
    SeasonCreate,
    SeasonDetailOut,
    SeasonOut,
    SeasonSettleOut,
    SeasonSummaryOut,
    SeasonUpdate,
    SettlementOut,
    SubstituteCreate,
    WaitlistCancelOut,
)
from volleyflow.db.models import (
    AbsenceRow,
    ClubMemberRow,
    ClubRow,
    DropInRow,
    GameRow,
    LedgerEntryRow,
    PlayerRow,
    ProblemReportRow,
    SeasonMemberRow,
    SeasonRow,
    WaitlistEntryRow,
)
from volleyflow.ledger import EntryType, balance
from volleyflow.notify.line_client import push_image_to_user, push_to_user
from volleyflow.pricing import share_per_game
from volleyflow.schedule import GameStatus
from volleyflow.settlement import (
    MemberSettlement,
    covered_absences,
    season_shares,
    settle_member,
)

router = APIRouter()


def _now() -> datetime:
    """Server-assigned, UTC, naive — never trust a client-supplied time
    for anything that feeds FIFO ordering (see settlement.py).
    """
    return datetime.now(UTC).replace(tzinfo=None)


def _get_or_create_player(db: Session, club_id: int, name: str) -> PlayerRow:
    """One Player per name within a club, not globally — the same name
    in two different clubs is two different people (see CLAUDE.md 2.5:
    Player is global, but name has no uniqueness constraint of its own
    any more; ClubMembership is what's scoped). Creates the
    ClubMemberRow too when this is a brand new player, so this is the
    one place a name-typed player both exists and belongs to the club
    at the same time.
    """
    player = (
        db.query(PlayerRow)
        .join(ClubMemberRow, ClubMemberRow.player_id == PlayerRow.id)
        .filter(ClubMemberRow.club_id == club_id, PlayerRow.name == name)
        .first()
    )
    if player is None:
        player = PlayerRow(name=name)
        db.add(player)
        db.flush()  # assigns player.id without ending the transaction
        db.add(
            ClubMemberRow(
                club_id=club_id, player_id=player.id, role="member", joined_at=_now()
            )
        )
    return player


def _unique_display_name(
    db: Session, display_name: str, exclude_player_id: int | None = None
) -> str:
    """Two different LINE accounts can share a display name — since
    identify_player operates globally, not per club (a Player's LINE
    identity isn't club-scoped), a collision here isn't even between two
    people in the same club necessarily. Disambiguate with a numeric
    suffix rather than fail the request. exclude_player_id lets a
    returning player keep their own current name without tripping over
    themselves.
    """
    candidate = display_name
    suffix = 2
    while True:
        query = db.query(PlayerRow).filter(PlayerRow.name == candidate)
        if exclude_player_id is not None:
            query = query.filter(PlayerRow.id != exclude_player_id)
        if query.first() is None:
            return candidate
        candidate = f"{display_name} ({suffix})"
        suffix += 1


def _get_player_by_name(db: Session, club_id: int, name: str) -> PlayerRow:
    player = (
        db.query(PlayerRow)
        .join(ClubMemberRow, ClubMemberRow.player_id == PlayerRow.id)
        .filter(ClubMemberRow.club_id == club_id, PlayerRow.name == name)
        .first()
    )
    if player is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, f"No player named {name!r} in this club"
        )
    return player


def _get_player_or_404(db: Session, player_id: int) -> PlayerRow:
    player = db.get(PlayerRow, player_id)
    if player is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No player with id {player_id}")
    return player


def _get_club_or_404(db: Session, club_id: int) -> ClubRow:
    club = db.get(ClubRow, club_id)
    if club is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No club with id {club_id}")
    return club


def _require_club_member(db: Session, club_id: int, player_id: int) -> None:
    membership = db.get(ClubMemberRow, {"club_id": club_id, "player_id": player_id})
    if membership is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "Player is not a member of this club"
        )


def get_current_player(
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> PlayerRow:
    """The verified caller, from a LINE ID token in the Authorization
    header — never trust a client-supplied player_id/line_user_id in a
    request body for anything that changes data. Every mutating
    endpoint below takes this as a dependency instead.

    404s (not 401) if there's no Player for this line_user_id yet: that
    can only mean the caller never called POST /players/identify, which
    is the one endpoint that verifies a token itself and doesn't depend
    on this — everything else assumes identify already ran once.
    """
    if authorization is None or not authorization.startswith("Bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing bearer token")
    token = authorization.removeprefix("Bearer ")
    try:
        line_user_id = verify_id_token(token)
    except ValueError as e:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(e)) from e

    player = db.query(PlayerRow).filter(PlayerRow.line_user_id == line_user_id).first()
    if player is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "No player identified for this LINE account yet"
        )
    return player


def _require_club_access(db: Session, club_id: int, current_player: PlayerRow) -> None:
    """You may read a club's roster, seasons and games only if you belong
    to it. CLAUDE.md 2.5: "clubs never see each other's members, seasons,
    or books." These reads were public until now, which meant every
    member's name, gender and LINE profile picture — plus who took leave
    and who dropped in — were readable by anyone who knew a club or
    season id, and GET /clubs handed out the ids.
    """
    membership = db.get(
        ClubMemberRow, {"club_id": club_id, "player_id": current_player.id}
    )
    if membership is None:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "You are not a member of this club"
        )


def _may_edit_accountless_player(
    db: Session, current_player: PlayerRow, target: PlayerRow
) -> bool:
    """Whether `current_player` organizes a club that `target` belongs to,
    and `target` has no LINE identity to speak for themselves with. The
    narrow case where acting on someone else's profile is legitimate:
    they only exist because an organizer typed their name in.
    """
    if target.line_user_id is not None:
        return False
    shared = (
        db.query(ClubMemberRow)
        .join(
            ClubRow,
            ClubRow.id == ClubMemberRow.club_id,
        )
        .filter(ClubMemberRow.player_id == target.id)
        .all()
    )
    for membership in shared:
        mine = db.get(
            ClubMemberRow,
            {"club_id": membership.club_id, "player_id": current_player.id},
        )
        if mine is not None and mine.role == "organizer":
            return True
    return False


def _require_organizer(db: Session, club_id: int, current_player: PlayerRow) -> None:
    membership = db.get(
        ClubMemberRow, {"club_id": club_id, "player_id": current_player.id}
    )
    if membership is None or membership.role != "organizer":
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "Only this club's organizer can do that"
        )


def _require_self_or_organizer(
    db: Session, club_id: int, current_player: PlayerRow, target_player_id: int
) -> None:
    """Lets a member act on their own attendance/substitute, and lets
    the club's organizer act on anyone's — the same "self, or the
    organizer" shape CLAUDE.md 2.3/2.4 describes for absences, signups,
    and substitutes throughout.
    """
    if current_player.id == target_player_id:
        return
    membership = db.get(
        ClubMemberRow, {"club_id": club_id, "player_id": current_player.id}
    )
    if membership is None or membership.role != "organizer":
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "You can only do that for yourself, unless you're the organizer",
        )


def _require_may_sign_up(
    db: Session, club_id: int, current_player: PlayerRow, target: PlayerRow
) -> None:
    """Signing up is deliberately looser than the rest of
    _require_self_or_organizer, because "+1, I'm bringing a friend" is
    how drop-ins actually happen — the friend isn't in LINE, has no way
    to tap anything, and the member bringing them is the one who pays
    and vouches for them.

    So a club member may sign up anyone who has no LINE identity of
    their own, on the same reasoning as _may_edit_accountless_player:
    an accountless player exists only because somebody typed their
    name, and can't act for themselves. Signing up someone who *does*
    have an account stays restricted to that person or the organizer —
    a drop-in costs money, and nobody may commit a real user to it.
    """
    if current_player.id == target.id:
        return
    membership = db.get(
        ClubMemberRow, {"club_id": club_id, "player_id": current_player.id}
    )
    if membership is None:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "You are not a member of this club"
        )
    if membership.role == "organizer" or target.line_user_id is None:
        return
    raise HTTPException(
        status.HTTP_403_FORBIDDEN,
        "That person has their own account — they need to sign themselves up",
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


def _has_open_slot(db: Session, game: GameRow, season: SeasonRow) -> bool:
    """Expected attendance for this game vs. the season's capacity.

    expected = (fixed members minus this game's absences) + active drop-ins.
    Members who haven't taken leave count as attending by default — see
    CLAUDE.md 2.3, "members are expected by default."
    """
    member_count = (
        db.query(SeasonMemberRow).filter(SeasonMemberRow.season_id == season.id).count()
    )
    # cancelled_at matters: a member who took leave and then cancelled it
    # is expected again. Counting their cancelled absence made `expected`
    # too low and the game look emptier than it is, so capacity admitted
    # one drop-in too many for every absence that had ever been undone.
    absences = (
        db.query(AbsenceRow)
        .filter(AbsenceRow.game_id == game.id, AbsenceRow.cancelled_at.is_(None))
        .count()
    )
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


def _drop_in_share(db: Session, season_row: SeasonRow, game_id: int) -> Decimal:
    """What one game costs one person.

    Takes a game id rather than just the season because games no longer
    all cost the same: a night with the air conditioning on costs the
    club more, and a drop-in should pay for the night they actually turn
    up to. With ac_surcharge at 0 every game returns the same figure,
    which is what it always did.
    """
    game_rows = (
        db.query(GameRow)
        .filter(GameRow.season_id == season_row.id)
        .order_by(GameRow.id)
        .all()
    )
    member_rows = (
        db.query(PlayerRow)
        .join(SeasonMemberRow, SeasonMemberRow.player_id == PlayerRow.id)
        .filter(SeasonMemberRow.season_id == season_row.id)
        .all()
    )
    season = season_from_rows(season_row, game_rows, member_rows)
    return season_shares(season)[game_id]


def _record_drop_in_charge(
    db: Session, drop_in: DropInRow, season_row: SeasonRow, *, reverse: bool
) -> None:
    """A confirmed drop-in owes share_per_game for that one game — charged
    the moment they're confirmed (signup or waitlist promotion), reversed
    the moment they cancel. See CLAUDE.md 2.4: "A DropIn pays the per-game
    share, collected by the organizer."
    """
    share = _drop_in_share(db, season_row, drop_in.game_id)
    db.add(
        LedgerEntryRow(
            player_id=drop_in.player_id,
            club_id=season_row.club_id,
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
        row.id for row in db.query(GameRow).filter(GameRow.season_id == season.id).all()
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
    db.query(WaitlistEntryRow).filter(
        WaitlistEntryRow.player_id == player_id,
        WaitlistEntryRow.game_id.in_(game_ids),
    ).delete(synchronize_session=False)
    db.flush()


def _sync_season_fee_ledger(db: Session, season_row: SeasonRow) -> None:
    """Bring every current member's season_fee_charged total up to date
    with what settle_member says they should owe right now, writing one
    adjustment entry per player for the difference — never editing a
    past entry. Call this after anything that can change the target: the
    initial roster at season creation, adding/removing a member, editing
    total_venue_cost, or cancelling a game with a refund.

    See docs/billing-rules.md "Keeping the charge in sync when the
    inputs change". A no-op for anyone whose target hasn't moved (e.g.
    editing the venue's location touches nothing here).
    """
    _, settlements = _gather_member_settlements(db, season_row.id)
    now = _now()

    charged_by_player: dict[int, Decimal] = defaultdict(lambda: Decimal("0"))
    for player_id, amount in (
        db.query(LedgerEntryRow.player_id, LedgerEntryRow.amount)
        .filter(
            LedgerEntryRow.season_id == season_row.id,
            LedgerEntryRow.entry_type == EntryType.SEASON_FEE_CHARGED,
        )
        .all()
    ):
        charged_by_player[player_id] += amount

    current_member_ids = set()
    for ms in settlements:
        current_member_ids.add(ms.player.id)
        target = -ms.season_fee
        already_charged = charged_by_player.get(ms.player.id, Decimal("0"))
        adjustment = target - already_charged
        if adjustment == 0:
            continue
        db.add(
            LedgerEntryRow(
                player_id=ms.player.id,
                club_id=season_row.club_id,
                entry_type=EntryType.SEASON_FEE_CHARGED,
                amount=adjustment,
                recorded_at=now,
                season_id=season_row.id,
                note=(
                    f"Season {season_row.id} fee"
                    if already_charged == 0
                    else f"Season {season_row.id} fee adjustment"
                ),
            )
        )

    # Anyone charged before but no longer a member (removed from the
    # roster) gets their charge reversed to zero — see docs/billing-rules.md.
    for player_id, already_charged in charged_by_player.items():
        if player_id in current_member_ids or already_charged == 0:
            continue
        db.add(
            LedgerEntryRow(
                player_id=player_id,
                club_id=season_row.club_id,
                entry_type=EntryType.SEASON_FEE_CHARGED,
                amount=-already_charged,
                recorded_at=now,
                season_id=season_row.id,
                note=f"Season {season_row.id} fee reversed — no longer a member",
            )
        )


def _member_settlement_out(ms: MemberSettlement) -> MemberSettlementOut:
    return MemberSettlementOut(
        player_id=ms.player.id,
        player_name=ms.player.name,
        season_fee=ms.season_fee,
        refund=ms.refund,
        net=ms.net,
    )


@router.post("/clubs", response_model=ClubOut)
def create_club(
    payload: ClubCreate,
    db: Session = Depends(get_db),
    current_player: PlayerRow = Depends(get_current_player),
) -> ClubOut:
    """Whoever creates a club becomes its organizer — see CLAUDE.md 2.5.
    The creator is the verified caller, not a client-supplied id.
    """
    club = ClubRow(name=payload.name, created_at=_now())
    db.add(club)
    db.flush()
    db.add(
        ClubMemberRow(
            club_id=club.id,
            player_id=current_player.id,
            role="organizer",
            joined_at=_now(),
        )
    )
    db.commit()
    db.refresh(club)
    return ClubOut(id=club.id, name=club.name)


@router.get("/clubs", response_model=list[ClubOut])
def list_clubs(
    db: Session = Depends(get_db),
    current_player: PlayerRow = Depends(get_current_player),
) -> list[ClubOut]:
    """Only the caller's own clubs. This used to return every club that
    existed, to anyone — which both leaked the club list and handed out
    the ids that made the other reads enumerable. Joining a club you're
    not in yet goes through its invite link, which carries the id; see
    GET /clubs/{id} for the name lookup that link needs.
    """
    rows = (
        db.query(ClubRow, ClubMemberRow.role)
        .join(ClubMemberRow, ClubMemberRow.club_id == ClubRow.id)
        .filter(ClubMemberRow.player_id == current_player.id)
        .order_by(ClubRow.id)
        .all()
    )
    return [ClubOut(id=c.id, name=c.name, role=role) for c, role in rows]


@router.get("/clubs/{club_id}", response_model=ClubOut)
def get_club(
    club_id: int,
    db: Session = Depends(get_db),
    current_player: PlayerRow = Depends(get_current_player),
) -> ClubOut:
    """A club's name, for someone who has its invite link but hasn't
    joined yet — the "加入「啪排郎」?" prompt needs something to name.
    Deliberately not membership-gated (that's the whole point) and
    deliberately nothing but id and name; everything richer about a club
    requires belonging to it. Still requires a verified caller, so this
    isn't anonymously scrapable.
    """
    club = _get_club_or_404(db, club_id)
    return ClubOut(id=club.id, name=club.name)


@router.get("/players/{player_id}/clubs", response_model=list[MyClubOut])
def list_player_clubs(
    player_id: int,
    db: Session = Depends(get_db),
    current_player: PlayerRow = Depends(get_current_player),
) -> list[MyClubOut]:
    """Every club this player belongs to and their role in each — what a
    profile page's club list needs, spanning clubs the way a single
    club's member list (GET /clubs/{id}/members) can't. Self only: which
    clubs someone else belongs to isn't this app's business to hand to a
    third party, including another club's organizer.
    """
    if current_player.id != player_id:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "You can only list your own clubs"
        )
    rows = (
        db.query(ClubRow, ClubMemberRow)
        .join(ClubMemberRow, ClubMemberRow.club_id == ClubRow.id)
        .filter(ClubMemberRow.player_id == player_id)
        .order_by(ClubRow.id)
        .all()
    )
    return [
        MyClubOut(
            id=club.id,
            name=club.name,
            role=membership.role,
            wants_fixed_membership=membership.wants_fixed_membership,
        )
        for club, membership in rows
    ]


@router.get("/clubs/{club_id}/members", response_model=list[ClubMemberOut])
def list_club_members(
    club_id: int,
    db: Session = Depends(get_db),
    current_player: PlayerRow = Depends(get_current_player),
) -> list[ClubMemberOut]:
    """Everyone in the club and their role — distinct from a *season's*
    fixed roster (GET /seasons/{id} returns that). Members only: this
    returns real names, genders and LINE profile pictures.
    """
    _get_club_or_404(db, club_id)
    _require_club_access(db, club_id, current_player)
    rows = (
        db.query(PlayerRow, ClubMemberRow)
        .join(ClubMemberRow, ClubMemberRow.player_id == PlayerRow.id)
        .filter(ClubMemberRow.club_id == club_id)
        .order_by(PlayerRow.id)
        .all()
    )
    return [
        ClubMemberOut(
            id=player.id,
            name=player.name,
            gender=_gender(player.gender),
            avatar_url=player.avatar_url,
            linked=player.line_user_id is not None,
            role=membership.role,
            wants_fixed_membership=membership.wants_fixed_membership,
        )
        for player, membership in rows
    ]


@router.post("/clubs/{club_id}/join", response_model=MemberOut)
def join_club(
    club_id: int,
    db: Session = Depends(get_db),
    current_player: PlayerRow = Depends(get_current_player),
) -> MemberOut:
    """A player can only join a club as themselves — the verified
    caller, never an arbitrary player_id someone else could sign up.
    """
    _get_club_or_404(db, club_id)

    existing = db.get(
        ClubMemberRow, {"club_id": club_id, "player_id": current_player.id}
    )
    if existing is not None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "Already a member of this club"
        )

    db.add(
        ClubMemberRow(
            club_id=club_id,
            player_id=current_player.id,
            role="member",
            joined_at=_now(),
        )
    )
    db.commit()
    return MemberOut(
        id=current_player.id,
        name=current_player.name,
        gender=_gender(current_player.gender),
        avatar_url=current_player.avatar_url,
        linked=current_player.line_user_id is not None,
    )


@router.post("/clubs/{club_id}/players/{player_id}/link", response_model=MemberOut)
def link_player(
    club_id: int,
    player_id: int,
    payload: PlayerLink,
    db: Session = Depends(get_db),
    current_player: PlayerRow = Depends(get_current_player),
) -> MemberOut:
    """Says "this LINE account is that person on the roster".

    identify_player deliberately never guesses this by name: a wrong
    guess hands someone else's ledger to a stranger, so the organizer —
    who actually knows who's who — makes the call. That was always the
    design; this is the endpoint that was missing to carry it out, which
    is why a member typed in by hand stayed marked 訪客 even after they
    logged in, with a second copy of themselves sitting in the join pool.

    The roster entry survives, keeping its id and therefore its whole
    ledger history; the LINE-created row donates its identity and is
    deleted. Refused if that row has any history of its own, since
    deleting it would then destroy real records — remove the duplicate
    roster entry instead.
    """
    _get_club_or_404(db, club_id)
    _require_organizer(db, club_id, current_player)

    target = _get_player_or_404(db, player_id)
    source = _get_player_or_404(db, payload.line_player_id)
    if target.id == source.id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Those are the same player")
    _require_club_member(db, club_id, target.id)
    _require_club_member(db, club_id, source.id)

    if target.line_user_id is not None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "That roster entry is already linked to a LINE account",
        )
    if source.line_user_id is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "That player has no LINE account to link",
        )

    for model, column, what in (
        (LedgerEntryRow, LedgerEntryRow.player_id, "ledger entries"),
        (SeasonMemberRow, SeasonMemberRow.player_id, "season memberships"),
        (AbsenceRow, AbsenceRow.player_id, "absences"),
        (DropInRow, DropInRow.player_id, "drop-ins"),
    ):
        if db.query(model).filter(column == source.id).first() is not None:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"That LINE account already has {what} of its own — "
                "remove the duplicate roster entry instead",
            )

    # Two flushes, not one: line_user_id is unique, and within a single
    # flush SQLAlchemy is free to apply the target's UPDATE before the
    # source's, leaving both rows holding the same id for an instant —
    # which the index rejects. Release it first, then hand it over.
    released = source.line_user_id
    source.line_user_id = None
    db.flush()
    target.line_user_id = released
    target.avatar_url = source.avatar_url
    db.flush()

    db.query(WaitlistEntryRow).filter(WaitlistEntryRow.player_id == source.id).delete(
        synchronize_session=False
    )
    db.query(ClubMemberRow).filter(ClubMemberRow.player_id == source.id).delete(
        synchronize_session=False
    )
    db.delete(source)
    db.commit()
    db.refresh(target)

    return MemberOut(
        id=target.id,
        name=target.name,
        gender=_gender(target.gender),
        avatar_url=target.avatar_url,
        linked=True,
    )


@router.put("/clubs/{club_id}/members/me/intent", response_model=MyClubOut)
def set_membership_intent(
    club_id: int,
    payload: MembershipIntent,
    db: Session = Depends(get_db),
    current_player: PlayerRow = Depends(get_current_player),
) -> MyClubOut:
    """Someone who has just joined saying which kind of member they are.

    A fixed member's season fee is a real obligation, so claiming to be
    one can't put you on the roster by itself — it queues you for the
    organizer, who is the one deciding what anybody owes. Saying you're
    not opens up drop-in signups immediately, which commit you to one
    game at a time and nothing more.

    Self only, and always changeable: someone who dropped in all season
    and now wants in properly says so the same way.
    """
    club = _get_club_or_404(db, club_id)
    membership = db.get(
        ClubMemberRow, {"club_id": club_id, "player_id": current_player.id}
    )
    if membership is None:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "You are not a member of this club"
        )

    membership.wants_fixed_membership = payload.wants_fixed_membership
    db.commit()
    return MyClubOut(
        id=club.id,
        name=club.name,
        role=membership.role,
        wants_fixed_membership=membership.wants_fixed_membership,
    )


@router.delete("/clubs/{club_id}/members/{player_id}", status_code=204)
def remove_club_member(
    club_id: int,
    player_id: int,
    db: Session = Depends(get_db),
    current_player: PlayerRow = Depends(get_current_player),
) -> None:
    """Take someone out of the club — a wrong-link join, a test account,
    or someone who has left for good. Distinct from removing them from a
    *season's* roster (DELETE /seasons/{id}/members/{id}), which is about
    one season's billing.

    Refused while they're still on any of this club's season rosters:
    that removal has to go through the season endpoint, which corrects
    everyone's charge. Silently dropping them here would leave a season
    whose member list and ledger disagree.

    Refused for the last organizer, which would leave the club with
    nobody able to manage it.
    """
    _get_club_or_404(db, club_id)
    _require_organizer(db, club_id, current_player)

    membership = db.get(ClubMemberRow, {"club_id": club_id, "player_id": player_id})
    if membership is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not a member of this club")

    on_a_roster = (
        db.query(SeasonMemberRow)
        .join(SeasonRow, SeasonRow.id == SeasonMemberRow.season_id)
        .filter(
            SeasonRow.club_id == club_id,
            SeasonMemberRow.player_id == player_id,
        )
        .first()
    )
    if on_a_roster is not None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Still a fixed member of a season — remove them from that season first",
        )

    if membership.role == "organizer":
        other_organizers = (
            db.query(ClubMemberRow)
            .filter(
                ClubMemberRow.club_id == club_id,
                ClubMemberRow.role == "organizer",
                ClubMemberRow.player_id != player_id,
            )
            .count()
        )
        if other_organizers == 0:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "This is the club's only organizer",
            )

    db.delete(membership)
    db.commit()


@router.delete("/seasons/{season_id}", status_code=204)
def delete_season(
    season_id: int,
    db: Session = Depends(get_db),
    current_player: PlayerRow = Depends(get_current_player),
) -> None:
    """Deletes a season outright, with its games, attendance and the
    ledger entries it wrote. For a season created by mistake or for
    testing — not for tidying up a real one.

    Refused once settled. A settled season is closed books: CLAUDE.md 2.5
    calls for every data change to be auditable, and destroying finished
    accounts is the one thing that can't be. Before settlement the only
    ledger entries a season owns are its own fee charges, which are
    meaningless without it.
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

    _delete_season_rows(db, season)
    db.commit()


def _delete_season_rows(db: Session, season: SeasonRow) -> None:
    """Removes a season and everything hanging off it, children first so
    no foreign key is ever left dangling."""
    game_ids = [
        row.id for row in db.query(GameRow).filter(GameRow.season_id == season.id).all()
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


@router.delete("/clubs/{club_id}", status_code=204)
def delete_club(
    club_id: int,
    db: Session = Depends(get_db),
    current_player: PlayerRow = Depends(get_current_player),
) -> None:
    """Deletes a club and every season in it. Same reasoning and the same
    refusal as deleting a season: a settled season anywhere in the club
    blocks it, because that's real closed books. Players themselves are
    never deleted — a Player is global and outlives any one club
    (CLAUDE.md 2.1); only their membership of this club goes.
    """
    _get_club_or_404(db, club_id)
    _require_organizer(db, club_id, current_player)

    seasons = db.query(SeasonRow).filter(SeasonRow.club_id == club_id).all()
    if any(s.settled_at is not None for s in seasons):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "A season in this club is settled — its books can't be deleted",
        )

    for season in seasons:
        _delete_season_rows(db, season)
    db.query(LedgerEntryRow).filter(LedgerEntryRow.club_id == club_id).delete(
        synchronize_session=False
    )
    db.query(ClubMemberRow).filter(ClubMemberRow.club_id == club_id).delete(
        synchronize_session=False
    )
    club = db.get(ClubRow, club_id)
    if club is not None:
        db.delete(club)
    db.commit()


@router.get("/clubs/{club_id}/seasons", response_model=list[SeasonSummaryOut])
def list_seasons(
    club_id: int,
    db: Session = Depends(get_db),
    current_player: PlayerRow = Depends(get_current_player),
) -> list[SeasonSummaryOut]:
    """Enough per season to label it in a picker — dates, not a bare id
    a human has no way to recognize. Members only, same as the roster.
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

    season = SeasonRow(
        club_id=club_id,
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
        player = _get_or_create_player(db, club_id, name)
        db.add(SeasonMemberRow(season_id=season.id, player_id=player.id))
        member_ids.append(player.id)

    db.flush()
    _sync_season_fee_ledger(db, season)

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
    season_id: int,
    payload: SeasonUpdate,
    db: Session = Depends(get_db),
    current_player: PlayerRow = Depends(get_current_player),
) -> SeasonOut:
    """A partial update — only fields the client actually sent are
    touched (see SeasonUpdate). Changing `total_venue_cost` changes
    every member's per-game share for the whole season (past games
    included, since there's one season-wide split, not a per-period
    one) — the frontend warns about this before calling in; once
    settled, the ledger already reflects the old cost, so it's locked.
    Each current member's season_fee_charged total is corrected with an
    adjustment entry — see _sync_season_fee_ledger.
    """
    season = db.get(SeasonRow, season_id)
    if season is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No season with id {season_id}")
    _require_organizer(db, season.club_id, current_player)

    updates = payload.model_dump(exclude_unset=True)
    if "total_venue_cost" in updates and season.settled_at is not None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Season is already settled — venue cost can't change now",
        )

    for field, value in updates.items():
        setattr(season, field, value)

    if "total_venue_cost" in updates:
        db.flush()
        _sync_season_fee_ledger(db, season)

    db.commit()
    db.refresh(season)
    return _season_out(db, season)


@router.post("/games/{game_id}/cancel", response_model=GameOut)
def cancel_game(
    game_id: int,
    payload: GameCancel,
    db: Session = Depends(get_db),
    current_player: PlayerRow = Depends(get_current_player),
) -> GameOut:
    """Calls off one scheduled game entirely — distinct from a member
    recording an absence, which leaves the game itself on. See
    docs/billing-rules.md "Game cancellation": `refunded=True`
    (CANCELLED_REFUNDED) lowers every current member's billable_games by
    one and credits share_per_game back to each of them right away;
    `refunded=False` (CANCELLED_UNREFUNDED) changes nobody's charge,
    since the venue cost was already paid either way. Existing
    absence/drop-in rows for this game are left alone — the refund
    calculation already ignores CANCELLED_REFUNDED games.
    """
    game = _get_game_or_404(db, game_id)
    season = db.get(SeasonRow, game.season_id)
    assert season is not None
    _require_organizer(db, season.club_id, current_player)
    if season.settled_at is not None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Season is already settled")
    if game.status != GameStatus.SCHEDULED:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Game is already cancelled")

    game.status = (
        GameStatus.CANCELLED_REFUNDED
        if payload.refunded
        else GameStatus.CANCELLED_UNREFUNDED
    )
    db.flush()
    if payload.refunded:
        _sync_season_fee_ledger(db, season)
    db.commit()
    db.refresh(game)
    return GameOut(id=game.id, date=game.date, status=game.status)


@router.put("/games/{game_id}/air-conditioning", response_model=GameOut)
def set_game_air_conditioning(
    game_id: int,
    payload: AirConditioningUpdate,
    db: Session = Depends(get_db),
    current_player: PlayerRow = Depends(get_current_player),
) -> GameOut:
    """Record whether the air conditioning actually ran for this game.

    Which nights get cooled is a forecast when the season is booked and
    a fact on the evening itself, so this is the one season parameter
    that is expected to change mid-season. Flipping it moves the club's
    real bill by `ac_surcharge` — the venue charges for the AC it ran —
    so `total_venue_cost` moves with it, and every member's charge is
    corrected by an adjustment entry rather than by editing what they
    were originally charged. Same machinery as changing the venue cost
    by hand; see _sync_season_fee_ledger and CLAUDE.md 2.4.

    A drop-in already charged for this game keeps the amount they were
    charged. They pay the organizer in cash on the night, and chasing
    someone for another $35 — or handing it back — because the AC
    decision changed is worse than the small unfairness of leaving it.
    The members absorb the difference, which is what the season fee is
    for.
    """
    game = _get_game_or_404(db, game_id)
    season = db.get(SeasonRow, game.season_id)
    assert season is not None  # game.season_id is a foreign key, always valid
    _require_organizer(db, season.club_id, current_player)
    if season.settled_at is not None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Season is already settled")

    if game.air_conditioned == payload.air_conditioned:
        return GameOut(id=game.id, date=game.date, status=game.status)

    delta = season.ac_surcharge if payload.air_conditioned else -season.ac_surcharge
    if season.total_venue_cost + delta < 0:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "That would make the season's venue cost negative — "
            "check the air-conditioning surcharge",
        )

    game.air_conditioned = payload.air_conditioned
    season.total_venue_cost += delta
    db.flush()
    _sync_season_fee_ledger(db, season)
    db.commit()
    db.refresh(game)
    return GameOut(id=game.id, date=game.date, status=game.status)


@router.post("/seasons/{season_id}/members", response_model=MemberOut)
def add_member(
    season_id: int,
    payload: MemberAdd,
    db: Session = Depends(get_db),
    current_player: PlayerRow = Depends(get_current_player),
) -> MemberOut:
    """Adding a member changes everyone's per-game share for the whole
    season, same reasoning as changing the venue cost — the frontend
    warns before calling this. Blocked once settled: the ledger already
    reflects the season fee computed from the roster at that time. The
    new member is charged their full season fee immediately, and every
    other current member's charge is corrected for the new, lower share
    — see _sync_season_fee_ledger.
    """
    season = db.get(SeasonRow, season_id)
    if season is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No season with id {season_id}")
    _require_organizer(db, season.club_id, current_player)
    if season.settled_at is not None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Season is already settled")

    player = _get_or_create_player(db, season.club_id, payload.player_name)
    # Never overwrites: a returning player's own setting is theirs, and
    # this argument is just what the organizer happened to type today.
    if payload.gender is not None and player.gender is None:
        player.gender = payload.gender
    existing = db.get(SeasonMemberRow, {"season_id": season_id, "player_id": player.id})
    if existing is not None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "Already a member of this season"
        )

    db.add(SeasonMemberRow(season_id=season_id, player_id=player.id))
    db.flush()
    _absorb_drop_ins_into_membership(db, season, player.id)
    _sync_season_fee_ledger(db, season)
    db.commit()
    db.refresh(player)
    return MemberOut(
        id=player.id,
        name=player.name,
        gender=_gender(player.gender),
        avatar_url=player.avatar_url,
        linked=player.line_user_id is not None,
    )


@router.delete("/seasons/{season_id}/members/{player_id}", status_code=204)
def remove_member(
    season_id: int,
    player_id: int,
    db: Session = Depends(get_db),
    current_player: PlayerRow = Depends(get_current_player),
) -> None:
    """Same retroactive-share caveat as adding one. Doesn't touch this
    player's past absence/drop-in rows for this season — they simply
    stop counting toward anyone's settlement once removed, since that
    only ever iterates the current member list. Their season-fee charge
    is reversed to zero and every remaining member's charge is corrected
    for the new, higher share — see _sync_season_fee_ledger.
    """
    season = db.get(SeasonRow, season_id)
    if season is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No season with id {season_id}")
    _require_organizer(db, season.club_id, current_player)
    if season.settled_at is not None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Season is already settled")

    membership = db.get(
        SeasonMemberRow, {"season_id": season_id, "player_id": player_id}
    )
    if membership is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not a member of this season")

    db.delete(membership)
    db.flush()
    _sync_season_fee_ledger(db, season)
    db.commit()


@router.get("/seasons/{season_id}/join-pool", response_model=list[ClubMemberOut])
def list_join_pool(
    season_id: int,
    db: Session = Depends(get_db),
    current_player: PlayerRow = Depends(get_current_player),
) -> list[ClubMemberOut]:
    """Players who are members of this season's club but aren't on this
    season's fixed roster yet — candidates for the organizer to promote
    with the existing POST /seasons/{id}/members. Club membership, not a
    bare line_user_id check, is the pool boundary now: someone the
    organizer typed in by hand is just as eligible as someone who joined
    through the LINE link. Organizer-only: this is specifically an
    organizer tool, unlike the public season/roster reads.

    Returns club memberships rather than bare players so the pool can say
    who has actually asked to be a fixed member — that request is the
    whole reason an organizer looks at this list.
    """
    season = db.get(SeasonRow, season_id)
    if season is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No season with id {season_id}")
    _require_organizer(db, season.club_id, current_player)

    season_member_ids = db.query(SeasonMemberRow.player_id).filter(
        SeasonMemberRow.season_id == season_id
    )
    pool = (
        db.query(PlayerRow, ClubMemberRow)
        .join(ClubMemberRow, ClubMemberRow.player_id == PlayerRow.id)
        .filter(
            ClubMemberRow.club_id == season.club_id,
            ~PlayerRow.id.in_(season_member_ids),
        )
        .order_by(PlayerRow.id)
        .all()
    )
    return [
        ClubMemberOut(
            id=player.id,
            name=player.name,
            gender=_gender(player.gender),
            avatar_url=player.avatar_url,
            linked=player.line_user_id is not None,
            role=membership.role,
            wants_fixed_membership=membership.wants_fixed_membership,
        )
        for player, membership in pool
    ]


@router.get("/seasons/{season_id}", response_model=SeasonDetailOut)
def get_season(
    season_id: int,
    db: Session = Depends(get_db),
    current_player: PlayerRow = Depends(get_current_player),
) -> SeasonDetailOut:
    """The whole season: roster, every game, who's absent, who's
    dropping in, who's waiting. Members of this season's club only — it
    names names.
    """
    season_row = db.get(SeasonRow, season_id)
    if season_row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No season with id {season_id}")
    _require_club_access(db, season_row.club_id, current_player)

    # "locked" is about this caller, not about the date alone — the
    # organizer is never locked out (see _require_within_change_deadline).
    viewer_membership = db.get(
        ClubMemberRow,
        {"club_id": season_row.club_id, "player_id": current_player.id},
    )
    viewer_is_organizer = (
        viewer_membership is not None and viewer_membership.role == "organizer"
    )

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

    drop_ins_by_game: dict[
        int, list[tuple[int, int, str, Gender | None, int | None]]
    ] = defaultdict(list)
    for drop_in, player in (
        db.query(DropInRow, PlayerRow)
        .join(PlayerRow, DropInRow.player_id == PlayerRow.id)
        .filter(DropInRow.game_id.in_(game_ids), DropInRow.cancelled_at.is_(None))
        .order_by(DropInRow.signed_up_at)
        .all()
    ):
        drop_ins_by_game[drop_in.game_id].append(
            (
                drop_in.id,
                player.id,
                player.name,
                _gender(player.gender),
                drop_in.covers_absence_id,
            )
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

    # One place computes what each game costs a person; the games below
    # and the drop-in charges elsewhere both read from it, so they can't
    # disagree. See settlement.season_shares.
    shares = season_shares(season_from_rows(season_row, game_rows, member_rows))

    games = []
    for game in game_rows:
        # (absence_id, name) pairs, FIFO order
        absences_list = absences_by_game[game.id]
        # (drop_in_id, player_id, name, gender, covers_absence_id) tuples
        drop_ins = drop_ins_by_game[game.id]

        # Explicit substitutes claim their absence first; the remaining
        # (unclaimed) absences pair FIFO with the remaining drop-ins —
        # same two-pass rule as settlement.covered_absences, just
        # producing a name-to-name display instead of a refund count.
        absence_name_by_id = dict(absences_list)
        covered_by_name: dict[str, str] = {}
        covering_name: dict[int, str] = {}
        claimed_absence_ids: set[int] = set()
        for entry in drop_ins:
            drop_in_id, _player_id, name, _drop_in_gender, covers_absence_id = entry
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
            for drop_in_id, _player_id, name, _gender, covers in drop_ins
            if covers is None
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
                air_conditioned=game.air_conditioned,
                share=shares[game.id],
                locked=not (
                    viewer_is_organizer or _within_change_deadline(game, season_row)
                ),
                absences=[
                    AbsenceDetailOut(
                        id=aid, player_name=name, covered_by=covered_by_name.get(name)
                    )
                    for aid, name in absences_list
                ],
                confirmed_drop_ins=[
                    DropInDetailOut(
                        id=drop_in_id,
                        player_id=player_id,
                        player_name=name,
                        gender=gender,
                        covering=covering_name.get(drop_in_id),
                    )
                    for drop_in_id, player_id, name, gender, _covers in drop_ins
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
        # The headline figure a season is described by: what one game
        # costs one person before any air conditioning. Each game carries
        # its own share above, because a cooled night costs more.
        share_per_game=share_per_game(
            season_row.total_venue_cost
            - season_row.ac_surcharge * sum(1 for g in game_rows if g.air_conditioned),
            len(game_rows),
            len(member_rows),
        ),
        ac_surcharge=season_row.ac_surcharge,
        settled_at=season_row.settled_at,
        members=[
            MemberOut(
                id=m.id,
                name=m.name,
                gender=_gender(m.gender),
                avatar_url=m.avatar_url,
                linked=m.line_user_id is not None,
            )
            for m in member_rows
        ],
        games=games,
    )


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
    _require_self_or_organizer(db, season.club_id, current_player, absence.player_id)
    _require_within_change_deadline(db, game, season, current_player)

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

    player = _get_or_create_player(db, season.club_id, payload.player_name)
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
    _require_within_change_deadline(db, game, season, current_player)
    if player.gender is None and payload.gender is not None:
        player.gender = payload.gender

    _reject_if_already_playing(db, game, season, player)

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
    _require_within_change_deadline(db, game, season, current_player)

    player_id = entry.player_id
    db.delete(entry)
    db.commit()
    return WaitlistCancelOut(id=entry_id, player_id=player_id, game_id=game.id)


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
    _require_self_or_organizer(db, season.club_id, current_player, drop_in.player_id)
    _require_within_change_deadline(db, game, season, current_player)

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
def view_settlement(
    season_id: int,
    db: Session = Depends(get_db),
    current_player: PlayerRow = Depends(get_current_player),
) -> SettlementOut:
    """Read-only preview — what settling right now would charge/refund.
    Doesn't touch the ledger. See POST .../settle to actually record it.
    Organizer-only: this exposes every member's fee and refund at once.
    """
    season_row, settlements = _gather_member_settlements(db, season_id)
    _require_organizer(db, season_row.club_id, current_player)
    return SettlementOut(
        season_id=season_id,
        members=[_member_settlement_out(ms) for ms in settlements],
    )


@router.post("/seasons/{season_id}/settle", response_model=SeasonSettleOut)
def settle_season(
    season_id: int,
    db: Session = Depends(get_db),
    current_player: PlayerRow = Depends(get_current_player),
) -> SeasonSettleOut:
    """Credits every member's absence refund to the ledger and locks the
    season, once. Doesn't charge the season fee — that already happened
    when each member joined the roster (see _sync_season_fee_ledger) —
    so this is purely the refund half of CLAUDE.md 2.4's settlement.
    A season can't be settled twice.
    """
    season_row, settlements = _gather_member_settlements(db, season_id)
    _require_organizer(db, season_row.club_id, current_player)
    if season_row.settled_at is not None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Season already settled")

    now = _now()
    for ms in settlements:
        if ms.refund > 0:
            db.add(
                LedgerEntryRow(
                    player_id=ms.player.id,
                    club_id=season_row.club_id,
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


@router.post("/reports", status_code=204)
def report_a_problem(
    payload: ProblemReport,
    request: Request,
    db: Session = Depends(get_db),
    current_player: PlayerRow = Depends(get_current_player),
) -> None:
    """Sends a problem report straight to the developer over LINE.

    Delivered rather than stored: an unread row in a table nobody has a
    screen for is the same as no report at all, and this project has no
    admin surface to grow one on. LINE is where the developer already
    is. The consequence is deliberate — if the push fails (LINE's free
    monthly quota is shared with game reminders), this fails loudly and
    the reporter is told, instead of the report quietly disappearing.

    Most of what makes a report actionable isn't the sentence someone
    types, it's who and where — so the caller's identity, screen and
    browser are attached here rather than asked for.
    """
    text = (payload.message or "").strip()
    if not text:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Nothing to report")

    club_name = ""
    if payload.club_id is not None:
        club = db.get(ClubRow, payload.club_id)
        if club is not None:
            club_name = club.name

    lines = [
        "🐞 VolleyFlow 問題回報",
        "",
        text,
        "",
        f"回報者：{current_player.name}（#{current_player.id}）",
    ]
    if club_name:
        lines.append(f"球隊：{club_name}")
    if payload.page:
        lines.append(f"畫面：{payload.page}")
    if payload.user_agent:
        lines.append(f"裝置：{payload.user_agent[:180]}")
    lines.append(f"時間：{_now().isoformat(timespec='seconds')} UTC")

    developer_id = os.environ.get("LINE_ORGANIZER_USER_ID")
    if not developer_id:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Problem reporting isn't configured yet",
        )
    image_url = _store_screenshot(db, request, payload.screenshot)

    try:
        push_to_user(developer_id, "\n".join(lines))
        if image_url is not None:
            push_image_to_user(developer_id, image_url)
    except Exception as e:  # noqa: BLE001 - any failure means undelivered
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            "Couldn't send the report — please tell the organizer directly",
        ) from e


_MAX_SCREENSHOT_BYTES = 2 * 1024 * 1024


def _store_screenshot(
    db: Session, request: Request, data_url: str | None
) -> str | None:
    """Keeps a screenshot just long enough for LINE to come and fetch it.

    An image message has to name an HTTPS URL that LINE's own servers can
    read, so the picture needs somewhere public to live, and this project
    has no file storage. It goes in the database under an unguessable id
    and is served back by the endpoint below.

    Anything older than a month goes at the same time: nobody revisits a
    screenshot of a bug fixed weeks ago, and a free-tier database
    shouldn't quietly fill with them.
    """
    if not data_url:
        return None

    match = re.fullmatch(r"data:(image/(?:png|jpeg|webp));base64,(.+)", data_url, re.S)
    if match is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "Screenshot must be a PNG, JPEG or WebP"
        )
    content_type, encoded = match.groups()
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as e:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "Screenshot isn't valid base64"
        ) from e
    if len(raw) > _MAX_SCREENSHOT_BYTES:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "Screenshot is too large"
        )

    db.query(ProblemReportRow).filter(
        ProblemReportRow.created_at < _now() - timedelta(days=30)
    ).delete(synchronize_session=False)

    token = secrets.token_urlsafe(24)
    db.add(
        ProblemReportRow(
            id=token, image=raw, content_type=content_type, created_at=_now()
        )
    )
    db.commit()
    return str(request.url_for("problem_report_image", token=token))


@router.get("/reports/{token}/image", name="problem_report_image")
def problem_report_image(token: str, db: Session = Depends(get_db)) -> Response:
    """Deliberately public: LINE's servers fetch this to render the image
    message and arrive with none of our credentials. The id is 24 random
    bytes, so holding one tells you nothing about any other, and all that
    sits behind it is a screenshot its own author just sent.
    """
    row = db.get(ProblemReportRow, token)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such screenshot")
    return Response(content=row.image, media_type=row.content_type)


@router.post("/players/identify", response_model=PlayerIdentifyOut)
def identify_player(
    payload: PlayerIdentify, db: Session = Depends(get_db)
) -> PlayerIdentifyOut:
    """Called once per LIFF page load, right after LIFF resolves. The
    one endpoint that verifies a token itself rather than depending on
    get_current_player: there's no Player row to look up yet on a first
    visit, so identity has to come from the token directly. Two cases:

    1. This line_user_id has been seen before — this is a returning
       player. Sync their display name/avatar (LINE names can change).
    2. Never seen before — a genuinely new person. Create them; they
       exist but aren't a fixed member of any season until the organizer
       promotes them from a season's join-pool (see list_join_pool).

    Deliberately does not try to auto-claim an existing name-only Player
    by matching display_name — the organizer's own account of who's who
    is more trustworthy than a name-string guess, and a wrong guess
    would silently hand someone else's ledger history to a stranger.
    Reconciling a real person's pre-LIFF record with their LINE identity
    is a manual, organizer-driven action.
    """
    try:
        line_user_id = verify_id_token(payload.id_token)
    except ValueError as e:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "Invalid or expired LINE ID token"
        ) from e

    player = db.query(PlayerRow).filter(PlayerRow.line_user_id == line_user_id).first()
    if player is not None:
        if player.name != payload.display_name:
            player.name = _unique_display_name(
                db, payload.display_name, exclude_player_id=player.id
            )
        player.avatar_url = payload.picture_url
        db.commit()
        db.refresh(player)
        return PlayerIdentifyOut(
            id=player.id,
            name=player.name,
            avatar_url=player.avatar_url,
            gender=_gender(player.gender),
        )

    name = _unique_display_name(db, payload.display_name)
    new_player = PlayerRow(
        name=name, line_user_id=line_user_id, avatar_url=payload.picture_url
    )
    db.add(new_player)
    db.commit()
    db.refresh(new_player)
    return PlayerIdentifyOut(
        id=new_player.id,
        name=new_player.name,
        avatar_url=new_player.avatar_url,
        gender=_gender(new_player.gender),
    )


@router.put("/players/{player_id}/gender", response_model=MemberOut)
def set_player_gender(
    player_id: int,
    payload: GenderUpdate,
    db: Session = Depends(get_db),
    current_player: PlayerRow = Depends(get_current_player),
) -> MemberOut:
    """Self-reported by the player — never billing-relevant, only shown
    on the roster so a game's expected male/female split is visible.

    Yours to set, with one exception: a player the organizer typed in by
    hand has no LINE account, so they cannot open the app and set it
    themselves, and their half of the roster's male/female count would
    be stuck at unknown forever. An organizer of a club they belong to
    may fill it in for them. Once that person claims a LINE identity,
    it's theirs alone again.
    """
    player = _get_player_or_404(db, player_id)
    if current_player.id != player_id and not _may_edit_accountless_player(
        db, current_player, player
    ):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "You can only set your own gender"
        )
    player.gender = payload.gender
    db.commit()
    db.refresh(player)
    return MemberOut(
        id=player.id,
        name=player.name,
        gender=_gender(player.gender),
        avatar_url=player.avatar_url,
        linked=player.line_user_id is not None,
    )


@router.put("/players/{player_id}/name", response_model=MemberOut)
def set_player_name(
    player_id: int,
    payload: NameUpdate,
    db: Session = Depends(get_db),
    current_player: PlayerRow = Depends(get_current_player),
) -> MemberOut:
    """What a player is called on the roster is theirs to set, same
    self-only reasoning as set_player_gender. Ledger entries and season
    membership key off player_id, never off this string, so renaming
    never touches billing history — CLAUDE.md 2.1's "one person, one
    name, for life" tracks the id; this is just the label. Runs through
    _unique_display_name so a rename can't collide with someone else's
    current name (excluding the player's own row, so keeping the name
    they already have is always allowed).
    """
    player = _get_player_or_404(db, player_id)
    if current_player.id != player_id and not _may_edit_accountless_player(
        db, current_player, player
    ):
        # Same rule as set_player_gender, which allowed this from the
        # start. Without it an organizer who mistypes a guest's name can
        # never correct it — the guest has no account to fix it from
        # themselves — while the gender on the very same row stays
        # editable, which is an arbitrary place to draw the line.
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "You can only rename yourself, or someone you added who has no account",
        )
    name = payload.name.strip()
    if not name:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Name can't be empty")
    player.name = _unique_display_name(db, name, exclude_player_id=player.id)
    db.commit()
    db.refresh(player)
    return MemberOut(
        id=player.id,
        name=player.name,
        gender=_gender(player.gender),
        avatar_url=player.avatar_url,
        linked=player.line_user_id is not None,
    )


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
