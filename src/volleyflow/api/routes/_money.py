"""What a signup or a season costs, and the ledger entries that
record it.

Writes money, reads people. Never reaches back up into the attendance
rules — those call down into this.
"""

from collections import defaultdict
from decimal import Decimal

from fastapi import (
    HTTPException,
    status,
)
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
from volleyflow.settlement import (
    MemberSettlement,
    season_shares,
    settle_member,
)


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
    the moment they cancel. See CLAUDE.md 2.4: "A DropIn pays the per-game
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
