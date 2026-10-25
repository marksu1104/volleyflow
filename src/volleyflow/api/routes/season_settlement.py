"""The end of a season: its settlement, settling and undoing it, and the
fee notice for the next one."""

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    status,
)
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from volleyflow.api.dependencies import get_db
from volleyflow.api.routes._audit import _audit, _season_span
from volleyflow.api.routes._money import (
    _fee_notice_plan,
    _gather_member_settlements,
    _member_settlement_out,
    _previous_season,
    _why_not_settleable,
)
from volleyflow.api.routes._people import (
    _now,
    _require_organizer,
    _today_in_taiwan,
    get_current_player,
)
from volleyflow.api.schemas import (
    FeeNoticeOut,
    FeeNoticePreviewOut,
    FeeNoticeRecipientOut,
    PaidOutMemberOut,
    SeasonSettleOut,
    SeasonSettleRequest,
    SeasonUnsettleOut,
    SettlementOut,
)
from volleyflow.db.models import (
    GameRow,
    LedgerEntryRow,
    PlayerRow,
    SeasonMemberRow,
    SeasonRow,
)
from volleyflow.ledger import EntryType
from volleyflow.notify.reminders import (
    fee_notice_text,
    notify_fee_due,
)

router = APIRouter()


@router.get("/seasons/{season_id}/settlement", response_model=SettlementOut)
def view_settlement(
    season_id: int,
    db: Session = Depends(get_db),
    current_player: PlayerRow = Depends(get_current_player),
) -> SettlementOut:
    """What settling now would refund each member, without writing
    anything. Organizer only.
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
    payload: SeasonSettleRequest | None = None,
    db: Session = Depends(get_db),
    current_player: PlayerRow = Depends(get_current_player),
) -> SeasonSettleOut:
    """Credits each member's absence refund and locks the season, once.
    The season fee was already charged when they joined the roster.

    `payload.cash` records money that changed hands while settling, in
    the same step. Anyone left out keeps their balance for next season
    (保留至下一季), which needs no entry.
    """
    season_row, settlements = _gather_member_settlements(db, season_id)
    _require_organizer(db, season_row.club_id, current_player)
    if season_row.settled_at is not None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Season already settled")
    not_yet = _why_not_settleable(db, season_id, _today_in_taiwan())
    if not_yet is not None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, not_yet)

    cash = payload.cash if payload is not None else []
    members = {ms.player.id for ms in settlements}
    for entry in cash:
        # Only members of this season can be settled in cash here.
        if entry.player_id not in members:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"Player {entry.player_id} is not a member of this season",
            )

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

    # After the refunds, so cash is settled against a balance that includes
    # them.
    for entry in cash:
        if entry.amount == 0:
            continue
        db.add(
            LedgerEntryRow(
                player_id=entry.player_id,
                club_id=season_row.club_id,
                entry_type=EntryType.PAYMENT,
                amount=entry.amount,
                recorded_at=now,
                season_id=season_id,
                note=f"Season {season_id} settlement",
            )
        )

    season_row.settled_at = now
    _audit(
        db,
        current_player,
        "season_settled",
        season_row.club_id,
        detail=_season_span(db, season_row),
    )
    db.commit()

    # No message now: the result is told once, in the next season's 繳費通知.

    return SeasonSettleOut(
        season_id=season_id,
        settled_at=now,
        members=[_member_settlement_out(ms) for ms in settlements],
    )


@router.post("/seasons/{season_id}/unsettle", response_model=SeasonUnsettleOut)
def unsettle_season(
    season_id: int,
    db: Session = Depends(get_db),
    current_player: PlayerRow = Depends(get_current_player),
) -> SeasonUnsettleOut:
    """Undoes a settlement: writes the opposite of each refund it
    credited and unlocks the season. The originals stay, so the ledger
    shows both. `reverses_entry_id` is unique, so each refund can be
    reversed once.

    Refunds already paid out in cash stay paid and now read as owed
    back; those members are named in the response so the organizer
    knows.
    """
    season = (
        db.query(SeasonRow).filter(SeasonRow.id == season_id).with_for_update().first()
    )
    if season is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No season with id {season_id}")
    _require_organizer(db, season.club_id, current_player)
    if season.settled_at is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Season is not settled")

    refunds = (
        db.query(LedgerEntryRow)
        .filter(
            LedgerEntryRow.season_id == season_id,
            LedgerEntryRow.entry_type == EntryType.ABSENCE_REFUND,
            # Not itself an undo of something else.
            LedgerEntryRow.reverses_entry_id.is_(None),
        )
        .all()
    )
    reversed_already = {
        entry_id
        for (entry_id,) in db.query(LedgerEntryRow.reverses_entry_id).filter(
            LedgerEntryRow.reverses_entry_id.in_([r.id for r in refunds] or [0])
        )
    }

    # Refunds paid out in cash after this season was settled.
    paid_out = (
        db.query(LedgerEntryRow, PlayerRow)
        .join(PlayerRow, LedgerEntryRow.player_id == PlayerRow.id)
        .filter(
            LedgerEntryRow.season_id == season_id,
            LedgerEntryRow.entry_type == EntryType.PAYMENT,
            # Negative: the organizer paid the member, not the other way.
            LedgerEntryRow.amount < 0,
            LedgerEntryRow.recorded_at >= season.settled_at,
        )
        .all()
    )

    now = _now()
    undone = 0
    for refund in refunds:
        if refund.id in reversed_already:
            continue
        db.add(
            LedgerEntryRow(
                player_id=refund.player_id,
                club_id=refund.club_id,
                entry_type=EntryType.ABSENCE_REFUND,
                amount=-refund.amount,
                recorded_at=now,
                season_id=season_id,
                note=f"Season {season_id} settlement undone",
                reverses_entry_id=refund.id,
            )
        )
        undone += 1

    season.settled_at = None
    _audit(
        db,
        current_player,
        "season_unsettled",
        season.club_id,
        detail=_season_span(db, season),
    )
    try:
        db.commit()
    except IntegrityError:
        # Two taps on 復原結算 at once; the unique index caught the loser.
        db.rollback()
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "This settlement has already been undone"
        ) from None

    return SeasonUnsettleOut(
        season_id=season_id,
        reversed_entries=undone,
        already_paid_out=[
            PaidOutMemberOut(
                player_id=player.id, player_name=player.name, amount=-entry.amount
            )
            for entry, player in paid_out
        ],
    )


@router.get("/seasons/{season_id}/fee-notice", response_model=FeeNoticePreviewOut)
def preview_fee_notice(
    season_id: int,
    db: Session = Depends(get_db),
    current_player: PlayerRow = Depends(get_current_player),
) -> FeeNoticePreviewOut:
    """Who the 繳費通知 would go to now and exactly what each would read,
    from the server's own text, so the confirmation cannot drift from
    the message.
    """
    season_row = db.get(SeasonRow, season_id)
    if season_row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No season with id {season_id}")
    _require_organizer(db, season_row.club_id, current_player)
    names: dict[int, str] = {
        player_id: name
        for player_id, name in db.query(PlayerRow.id, PlayerRow.name)
        .join(SeasonMemberRow, SeasonMemberRow.player_id == PlayerRow.id)
        .filter(SeasonMemberRow.season_id == season_id)
    }
    return FeeNoticePreviewOut(
        recipients=[
            FeeNoticeRecipientOut(
                player_id=due.player_id,
                name=names.get(due.player_id, ""),
                due=due.due,
                text=fee_notice_text(db, season_row, due),
            )
            for _, due in _fee_notice_plan(db, season_row)
        ]
    )


@router.post("/seasons/{season_id}/fee-notice", response_model=FeeNoticeOut)
def send_fee_notice(
    season_id: int,
    db: Session = Depends(get_db),
    current_player: PlayerRow = Depends(get_current_player),
) -> FeeNoticeOut:
    """Sends this season's 繳費通知 to every member who owes something and
    has not had it.

    Last season's kept balance is folded in, so this waits until that
    season is settled. A LINE message cannot be taken back: the season
    row is locked and recipients are marked before sending, so a
    second tap finds nobody left; anyone LINE refuses is unmarked
    afterwards so they can be sent it later.
    """
    season_row = (
        db.query(SeasonRow).filter(SeasonRow.id == season_id).with_for_update().first()
    )
    if season_row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No season with id {season_id}")
    _require_organizer(db, season_row.club_id, current_player)

    last_game = (
        db.query(func.max(GameRow.date)).filter(GameRow.season_id == season_id).scalar()
    )
    if last_game is None or last_game < _today_in_taiwan():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Season has ended")
    previous = _previous_season(db, season_row)
    if previous is not None and previous.settled_at is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "The previous season is not settled"
        )

    plan = _fee_notice_plan(db, season_row)
    pending = [member for member, _ in plan]
    dues = [due for _, due in plan]
    owing = {d.player_id for d in dues}
    now = _now()
    for member in pending:
        if member.player_id in owing:
            member.fee_notice_sent_at = now
    _audit(
        db,
        current_player,
        "fee_notice_sent",
        season_row.club_id,
        detail=str(sum(1 for m in pending if m.player_id in owing)),
    )
    db.commit()

    told = notify_fee_due(db, season_row, dues)
    missed = owing - told
    if missed:
        for member in pending:
            if member.player_id in missed:
                member.fee_notice_sent_at = None
        db.commit()

    return FeeNoticeOut(sent=len(told), unreachable=len(missed))
