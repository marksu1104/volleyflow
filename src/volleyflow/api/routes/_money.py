"""What a signup or a season costs, and the ledger entries that record
it.

Writes money, reads people. Never calls up into the attendance rules;
they call down into this.
"""

from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from typing import NamedTuple

from fastapi import (
    HTTPException,
    status,
)
from sqlalchemy import ColumnElement, case, func, true
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
    PLAYING,
    AbsenceRow,
    DropInRow,
    GameRow,
    LedgerEntryRow,
    PlayerRow,
    SeasonMemberRow,
    SeasonRow,
)
from volleyflow.ledger import EntryType
from volleyflow.notify.reminders import FeeDue
from volleyflow.players import Player
from volleyflow.schedule import Game, GameStatus, Season
from volleyflow.settlement import (
    MemberSettlement,
    open_slots,
    season_shares,
    settle_member,
)


def _seasons_up_to(db: Session, season_row: SeasonRow) -> list[int]:
    """This club's seasons that start no later than `season_row`: the
    ones whose money is due by then.

    A season booked ahead has already charged its fee, so every figure
    for a season counts only it and earlier ones. Seasons are ordered
    by their first game; one with no games has charged nobody and
    counts only for itself.
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


def _previous_season(db: Session, season_row: SeasonRow) -> SeasonRow | None:
    """The club's season that starts most recently before this one, whose
    kept balances this season collects.
    """
    own = (
        db.query(func.min(GameRow.date))
        .filter(GameRow.season_id == season_row.id)
        .scalar()
    )
    if own is None:
        return None
    starts = (
        db.query(GameRow.season_id, func.min(GameRow.date).label("start"))
        .join(SeasonRow, SeasonRow.id == GameRow.season_id)
        .filter(SeasonRow.club_id == season_row.club_id, SeasonRow.id != season_row.id)
        .group_by(GameRow.season_id)
        .subquery()
    )
    found = (
        db.query(starts.c.season_id)
        .filter(starts.c.start < own)
        .order_by(starts.c.start.desc())
        .first()
    )
    return db.get(SeasonRow, found[0]) if found is not None else None


def _fee_dues(
    db: Session, season_row: SeasonRow, player_ids: list[int]
) -> dict[int, tuple[Decimal, Decimal, Decimal]]:
    """Per player: (this season's fee, what earlier seasons left, what is
    due up to this season), signed the ledger's way.
    """
    if not player_ids:
        return {}
    in_season = LedgerEntryRow.season_id == season_row.id
    up_to = _counts_up_to(_seasons_up_to(db, season_row))
    rows = (
        db.query(
            LedgerEntryRow.player_id,
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
            func.sum(case((in_season, LedgerEntryRow.amount), else_=0)),
            func.sum(case((up_to, LedgerEntryRow.amount), else_=0)),
        )
        .filter(
            LedgerEntryRow.club_id == season_row.club_id,
            LedgerEntryRow.player_id.in_(player_ids),
        )
        .group_by(LedgerEntryRow.player_id)
        .all()
    )
    return {
        player_id: (Decimal(fee), Decimal(through) - Decimal(season), Decimal(through))
        for player_id, fee, season, through in rows
    }


def _fee_notice_plan(
    db: Session, season_row: SeasonRow
) -> list[tuple[SeasonMemberRow, FeeDue]]:
    """Who this season's 繳費通知 would go to now, and what each is told:
    members with LINE, not yet sent it, who owe something. Shared by
    the preview and the send, so the organizer approves exactly who
    will hear.
    """
    pending = (
        db.query(SeasonMemberRow)
        .join(PlayerRow, PlayerRow.id == SeasonMemberRow.player_id)
        .filter(
            SeasonMemberRow.season_id == season_row.id,
            SeasonMemberRow.fee_notice_sent_at.is_(None),
            PlayerRow.line_user_id.is_not(None),
        )
        .order_by(PlayerRow.name)
        .all()
    )
    amounts = _fee_dues(db, season_row, [m.player_id for m in pending])

    refunds: dict[int, tuple[Decimal, int]] = {}
    previous = _previous_season(db, season_row)
    if previous is not None and previous.settled_at is not None:
        written: dict[int, Decimal] = {
            player_id: Decimal(total)
            for player_id, total in db.query(
                LedgerEntryRow.player_id, func.sum(LedgerEntryRow.amount)
            )
            .filter(
                LedgerEntryRow.season_id == previous.id,
                LedgerEntryRow.entry_type == EntryType.ABSENCE_REFUND,
            )
            .group_by(LedgerEntryRow.player_id)
            .all()
        }
        _, settlements = _gather_member_settlements(db, previous.id)
        for ms in settlements:
            refund = Decimal(written.get(ms.player.id) or 0)
            if refund > 0:
                refunds[ms.player.id] = (refund, ms.refunded_absences)

    plan = []
    zero = Decimal(0)
    for member in pending:
        fee, earlier, through = amounts.get(member.player_id, (zero, zero, zero))
        if through >= 0:
            continue
        refund, absences = refunds.get(member.player_id, (zero, 0))
        plan.append(
            (
                member,
                FeeDue(
                    player_id=member.player_id,
                    fee=-fee,
                    earlier=earlier,
                    due=-through,
                    previous_refund=refund,
                    previous_absences=absences,
                ),
            )
        )
    return plan


def _club_balances_up_to_now(
    db: Session, player_id: int, today: date
) -> dict[int, Decimal]:
    """This player's balance in each club, up to that club's season in
    play (see _season_in_play).
    """
    sums = (
        db.query(
            LedgerEntryRow.club_id,
            LedgerEntryRow.season_id,
            func.sum(LedgerEntryRow.amount),
        )
        .filter(LedgerEntryRow.player_id == player_id)
        .group_by(LedgerEntryRow.club_id, LedgerEntryRow.season_id)
        .all()
    )
    club_ids = {club_id for club_id, _, _ in sums}
    spans: dict[int, dict[int, tuple[date, date]]] = defaultdict(dict)
    for club_id, season_id, first, last in (
        db.query(
            SeasonRow.club_id,
            GameRow.season_id,
            func.min(GameRow.date),
            func.max(GameRow.date),
        )
        .join(SeasonRow, SeasonRow.id == GameRow.season_id)
        .filter(SeasonRow.club_id.in_(club_ids))
        .group_by(SeasonRow.club_id, GameRow.season_id)
        .all()
    ):
        spans[club_id][season_id] = (first, last)

    totals: dict[int, Decimal] = defaultdict(Decimal)
    for club_id, season_id, amount in sums:
        club_spans = spans[club_id]
        current = _season_in_play(club_spans, today)
        later = (
            current is not None
            and season_id in club_spans
            and club_spans[season_id][0] > club_spans[current][0]
        )
        if not later:
            totals[club_id] += Decimal(amount)
    return dict(totals)


def _season_in_play(spans: dict[int, tuple[date, date]], today: date) -> int | None:
    """The season a page opens on, matching defaultSeasonId in
    js/common/clubs.js: the one under way (ending soonest if they
    overlap), else the next to start, else the last to finish.
    """
    running = [s for s, (first, last) in spans.items() if first <= today <= last]
    if running:
        return min(running, key=lambda s: spans[s][1])
    upcoming = [s for s, (first, _) in spans.items() if first > today]
    if upcoming:
        return min(upcoming, key=lambda s: spans[s][0])
    if spans:
        return max(spans, key=lambda s: spans[s][1])
    return None


def _counts_up_to(season_ids: list[int] | None) -> ColumnElement[bool]:
    """The ledger rows a season's figure includes: its own and earlier
    seasons', plus entries tied to no season (money already moved).
    None means every row.
    """
    if season_ids is None:
        return true()
    return LedgerEntryRow.season_id.in_(season_ids) | LedgerEntryRow.season_id.is_(None)


def _drop_in_share(db: Session, season_row: SeasonRow, game_id: int) -> Decimal:
    """What one game costs one person; games can cost different amounts."""
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
    """Charges a confirmed drop-in that game's share, or reverses it when
    they leave.

    A reversal refunds `charged_amount`, what this signup actually
    paid, not the game's current share, which may have moved since
    (the air conditioning or capacity changed). A signup recorded
    before `charged_amount` existed falls back to the current share.
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
    # Never the queue — see drop_in_from_row, which refuses a queued row.
    drop_in_rows = (
        db.query(DropInRow)
        .filter(DropInRow.game_id.in_(game_ids), DropInRow.status == PLAYING)
        .all()
    )

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
    shared by the settlement view and the settle endpoint so they
    cannot disagree.
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
"""How long before the last game a season may be settled."""


def _why_not_settleable(db: Session, season_id: int, today: date) -> str | None:
    """Why this season cannot be settled yet, or None if it can.

    Settling locks attendance, so it opens three weeks before the last
    game and only once no remaining game has an absence nobody is
    filling: filling it later would change who is refunded.
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
    """Brings every current member's season-fee total up to what
    settle_member says they owe now, with one adjustment entry per
    player and never an edit. Call after anything that changes the
    target: the starting roster, the venue cost or capacity, a game
    cancelled with a refund.

    See docs/billing-rules.md "Keeping the charge in sync when the
    inputs change".
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

    # Anyone charged but no longer on the roster is reversed to zero.
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
    """The same correction for one member, after they are added or
    removed. Capacity is the denominator, so nobody else's charge can
    move.
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
