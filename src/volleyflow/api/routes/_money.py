"""What a signup or a season costs, and the ledger entries that
record it.

Writes money, reads people. Never reaches back up into the attendance
rules — those call down into this.
"""

from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from typing import NamedTuple

from fastapi import (
    HTTPException,
    status,
)
from sqlalchemy import ColumnElement, func, true
from sqlalchemy.orm import Session

from volleyflow.api.conversion import (
    absence_from_row,
    drop_in_from_row,
    game_from_row,
    player_from_row,
    season_from_rows,
)
from volleyflow.api.routes._people import (
    _now,
)
from volleyflow.api.schemas import (
    MemberSettlementOut,
)
from volleyflow.attendance import Absence, DropIn
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
from volleyflow.players import Player
from volleyflow.schedule import Game, GameStatus, Season
from volleyflow.settlement import (
    MemberSettlement,
    open_slots,
    season_shares,
    settle_member,
)


def _seasons_up_to(db: Session, season_row: SeasonRow) -> list[int]:
    """This club's seasons that start no later than `season_row` — the
    ones whose money is due by the time it is.

    Every money figure for a season is "up to" that season, never the
    whole club ledger. A member's fee is charged the moment they join a
    season, so a club that books January in October already has
    January's fee on everybody's ledger. Summing the whole ledger put
    that fee into October's settlement as money owed, and offered to
    collect it under October's name. Seasons are ordered by their first
    game; one with no games yet has charged nobody, so it only ever
    counts for itself.
    """
    starts: dict[int, date] = {
        season_id: start
        for season_id, start in db.query(GameRow.season_id, func.min(GameRow.date))
        .join(SeasonRow, SeasonRow.id == GameRow.season_id)
        .filter(SeasonRow.club_id == season_row.club_id)
        .group_by(GameRow.season_id)
        .all()
    }
    own = starts.get(season_row.id)
    earlier = [
        season_id
        for season_id, start in starts.items()
        if own is not None and start <= own
    ]
    return sorted(set(earlier) | {season_row.id})


def _counts_up_to(season_ids: list[int] | None) -> ColumnElement[bool]:
    """The ledger rows a season's figure includes: its own and earlier
    seasons', plus any entry tied to no season at all — that is money
    already moved, never a fee still to come. None: every row."""
    if season_ids is None:
        return true()
    return LedgerEntryRow.season_id.in_(season_ids) | LedgerEntryRow.season_id.is_(None)


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
        .order_by(GameRow.date)
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
    the moment they cancel. Per the billing rules: "A DropIn pays the per-game
    share, collected by the organizer."

    A cancellation refunds `drop_in.charged_amount` — what this specific
    signup actually paid — rather than recomputing the game's share as of
    right now. Those can differ: the organizer can flip a game's air
    conditioning or edit the season's capacity between signup and
    cancellation, and either one moves `share_per_game`. Refunding the
    recomputed share left a residual balance on somebody no longer
    connected to the game at all — charged $667 for a cooled night,
    refunded $572 once the setting was corrected — found by a random
    sweep rather than a real invoice not adding up. `charged_amount` is
    null on a drop-in recorded before this existed, so that one case
    still falls back to the old behaviour.
    """
    if reverse:
        share = drop_in.charged_amount
        if share is None:
            share = _drop_in_share(db, season_row, drop_in.game_id)
    else:
        share = _drop_in_share(db, season_row, drop_in.game_id)
        drop_in.charged_amount = share
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


class _SeasonFacts(NamedTuple):
    """One season read out of the database as billing objects."""

    row: SeasonRow
    season: Season
    member_rows: list[PlayerRow]
    players_by_id: dict[int, Player]
    games_by_id: dict[int, Game]
    absences: list[Absence]
    drop_ins: list[DropIn]


def _load_season_facts(db: Session, season_id: int) -> _SeasonFacts:
    season_row = db.get(SeasonRow, season_id)
    if season_row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No season with id {season_id}")

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
    drop_ins = [
        drop_in_from_row(d, players_by_id, games_by_id, absences_by_id)
        for d in drop_in_rows
    ]
    return _SeasonFacts(
        season_row,
        season,
        member_rows,
        players_by_id,
        games_by_id,
        list(absences_by_id.values()),
        drop_ins,
    )


def _gather_member_settlements(
    db: Session, season_id: int
) -> tuple[SeasonRow, list[MemberSettlement]]:
    """Everything needed to report or record a season's settlement,
    shared by the read-only settlement view and the settle-for-real
    endpoint below so they can never disagree with each other.
    """
    facts = _load_season_facts(db, season_id)
    settlements = [
        settle_member(
            facts.players_by_id[m.id], facts.season, facts.absences, facts.drop_ins
        )
        for m in facts.member_rows
    ]
    return facts.row, settlements


SETTLE_WINDOW = timedelta(weeks=3)
"""How long before the last game a season may be settled. Fixed rather
than a club setting, at the organizer's choice (2026-10-02): the roster
is usually certain well before the final night, and a setting nobody
changes is one more thing on the settings page to read past."""


def _why_not_settleable(db: Session, season_id: int, today: date) -> str | None:
    """Why this season cannot be settled yet, or None if it can.

    Settling locks attendance, so the games still to come must already
    be decided: settling is open from three weeks before the last game,
    and only once none of the remaining games has an absence nobody is
    filling. A slot still open could yet be filled — and that would
    change who gets refunded, after the refunds were written.
    """
    facts = _load_season_facts(db, season_id)
    games = sorted(facts.games_by_id.values(), key=lambda g: g.date)
    if not games:
        return None
    opens = games[-1].date - SETTLE_WINDOW
    if today < opens:
        return f"Season can be settled from {opens.isoformat()}"
    for game in games:
        if game.date < today or game.status != GameStatus.SCHEDULED:
            continue
        if open_slots(game, facts.absences, facts.drop_ins) > 0:
            return f"Game on {game.date.isoformat()} has an open slot"
    return None


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


def _sync_member_season_fee_ledger(
    db: Session,
    season_row: SeasonRow,
    player_id: int,
    *,
    is_member: bool,
) -> None:
    """Correct one member's season-fee total after a roster add/remove.

    Capacity is the fee denominator, so changing the roster does not move
    anybody else's target. The former all-member sync loaded every player,
    absence and drop-in even though only this one ledger can change; against
    the remote database that made a simple +/− wait several seconds.
    """
    target = Decimal("0")
    if is_member:
        game_rows = (
            db.query(GameRow)
            .filter(GameRow.season_id == season_row.id)
            .order_by(GameRow.date)
            .all()
        )
        season = season_from_rows(season_row, game_rows, [])
        shares = season_shares(season)
        fee = sum(
            (
                shares[game.id]
                for game in season.games
                if game.status != GameStatus.CANCELLED_REFUNDED
            ),
            Decimal("0"),
        )
        target = -fee

    already_charged = (
        db.query(func.coalesce(func.sum(LedgerEntryRow.amount), Decimal("0")))
        .filter(
            LedgerEntryRow.player_id == player_id,
            LedgerEntryRow.season_id == season_row.id,
            LedgerEntryRow.entry_type == EntryType.SEASON_FEE_CHARGED,
        )
        .scalar()
    )
    adjustment = target - Decimal(already_charged or 0)
    if adjustment == 0:
        return
    db.add(
        LedgerEntryRow(
            player_id=player_id,
            club_id=season_row.club_id,
            entry_type=EntryType.SEASON_FEE_CHARGED,
            amount=adjustment,
            recorded_at=_now(),
            season_id=season_row.id,
            note=(
                f"Season {season_row.id} fee"
                if is_member and already_charged == 0
                else (
                    f"Season {season_row.id} fee adjustment"
                    if is_member
                    else f"Season {season_row.id} fee reversed — no longer a member"
                )
            ),
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
