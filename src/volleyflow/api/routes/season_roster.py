"""A season's fixed roster: adding a member, removing one, and who could be added."""

from datetime import date

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    status,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from volleyflow.api.dependencies import get_db
from volleyflow.api.routes._attendance import (
    _absorb_drop_ins_into_membership,
    _close_absences_of_former_member,
    _displace_latest_drop_in,
    _games_with_no_room,
    _offer_freed_slots_to_the_queue,
    _restore_absorbed_drop_ins,
    _restore_retired_absences,
    _retired_absence_game_ids,
)
from volleyflow.api.routes._audit import _audit
from volleyflow.api.routes._money import (
    _sync_member_season_fee_ledger,
)
from volleyflow.api.routes._people import (
    _gender,
    _get_or_create_player,
    _now,
    _require_organizer,
    _today_in_taiwan,
    get_current_player,
)
from volleyflow.api.schemas import (
    ClubMemberOut,
    DisplacedDropInOut,
    MemberAdd,
    SeasonMemberAddOut,
)
from volleyflow.db.models import (
    AbsenceRow,
    ClubMemberRow,
    DropInRow,
    PlayerRow,
    SeasonMemberRow,
    SeasonRow,
)

router = APIRouter()


@router.post("/seasons/{season_id}/members", response_model=SeasonMemberAddOut)
def add_member(
    season_id: int,
    payload: MemberAdd,
    db: Session = Depends(get_db),
    current_player: PlayerRow = Depends(get_current_player),
) -> SeasonMemberAddOut:
    """Adds one fixed member and charges only their season fee.

    Shares are divided by capacity, not the roster, so existing
    members are not re-priced. On a full upcoming game, fixed
    membership takes priority and the latest confirmed drop-in goes
    back to the queue with their charge reversed. Refused once
    settled. See docs/billing-rules.md "Who the cost is split
    between".

    Locks the season row: the route reads "already a member?" and "is
    there room?" and writes based on both, so two overlapping requests
    must not both pass.
    """
    season = (
        db.query(SeasonRow).filter(SeasonRow.id == season_id).with_for_update().first()
    )
    if season is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No season with id {season_id}")
    _require_organizer(db, season.club_id, current_player)
    if season.settled_at is not None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Season is already settled")

    player = _get_or_create_player(db, season.club_id, payload.player_name)
    # Never overwrites a player's own setting.
    if payload.gender is not None and player.gender is None:
        player.gender = payload.gender
    existing = db.get(SeasonMemberRow, {"season_id": season_id, "player_id": player.id})
    if existing is not None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "Already a member of this season"
        )

    # The roster can never exceed capacity: the season fee assumes one payer
    # per slot. Checked separately from the per-game count below, because
    # absences are temporary and can make a game look emptier than it is.
    roster_size = (
        db.query(SeasonMemberRow).filter(SeasonMemberRow.season_id == season.id).count()
    )
    if roster_size >= season.capacity:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"This season already has {roster_size} fixed members for "
            f"{season.capacity} slots. Raise the capacity or take somebody "
            "off the roster first.",
        )

    # A full game needs a slot handed back before this member is added: at a
    # future game the latest drop-in yields it; at a past game the new
    # member is recorded away, so history gains nobody. Games where they
    # already hold a signup (absorbed below), or where rejoining restores
    # their leave (_restore_retired_absences), need neither: they add nobody
    # to the court there.
    away_again = _retired_absence_game_ids(db, season, player.id)
    full_games = _games_with_no_room(db, season, member_count=roster_size)
    full_game_ids = [game.id for game in full_games]
    signed_up_for = (
        {
            int(game_id)
            for (game_id,) in db.query(DropInRow.game_id).filter(
                DropInRow.player_id == player.id,
                DropInRow.game_id.in_(full_game_ids),
                DropInRow.playing(),
            )
        }
        if full_game_ids
        else set()
    )
    would_add_a_body = [
        game
        for game in full_games
        if game.id not in signed_up_for and game.id not in away_again
    ]

    # A correction after the game must not add a person to that night's
    # court: if it was full, the new member is recorded away. Their season
    # fee is still correct, and the coverage rules decide any refund.
    today = _today_in_taiwan()
    past_full_games = [game for game in would_add_a_body if game.date < today]
    future_full_games = [game for game in would_add_a_body if game.date >= today]
    displaced: list[tuple[int, date, int]] = []
    for game in future_full_games:
        displaced_player_id = _displace_latest_drop_in(db, game, season)
        if displaced_player_id is None:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"No room for another member on {game.date}, and there is no "
                "drop-in to move back to the waitlist.",
            )
        displaced.append((game.id, game.date, displaced_player_id))

    db.add(SeasonMemberRow(season_id=season_id, player_id=player.id))
    try:
        db.flush()
    except IntegrityError as e:
        # Two overlapping requests both passed the "already a member" check;
        # the primary key caught the second, which gets the same 400 rather
        # than a 500.
        db.rollback()
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "Already a member of this season"
        ) from e
    # Before the fee sync, so charges are worked out with this member's
    # leave back in place.
    _restore_retired_absences(db, season, player.id)
    _absorb_drop_ins_into_membership(db, season, player.id)
    recorded_at = _now()
    for game in past_full_games:
        db.add(
            AbsenceRow(
                player_id=player.id,
                game_id=game.id,
                recorded_at=recorded_at,
            )
        )
    _sync_member_season_fee_ledger(db, season, player.id, is_member=True)
    displaced_ids = {player_id for _, _, player_id in displaced}
    displaced_names = (
        {
            row.id: row.name
            for row in db.query(PlayerRow).filter(PlayerRow.id.in_(displaced_ids)).all()
        }
        if displaced_ids
        else {}
    )
    _audit(db, current_player, "roster_added", season.club_id, subject_name=player.name)
    db.commit()
    db.refresh(player)
    return SeasonMemberAddOut(
        id=player.id,
        name=player.name,
        gender=_gender(player.gender),
        avatar_url=player.avatar_url,
        linked=player.line_user_id is not None,
        displaced_drop_ins=[
            DisplacedDropInOut(
                player_id=player_id,
                player_name=displaced_names[player_id],
                game_id=game_id,
                game_date=game_date,
            )
            for game_id, game_date, player_id in displaced
        ],
    )


@router.delete("/seasons/{season_id}/members/{player_id}", status_code=204)
def remove_member(
    season_id: int,
    player_id: int,
    db: Session = Depends(get_db),
    current_player: PlayerRow = Depends(get_current_player),
) -> None:
    """Removes one fixed member and reverses only their season fee; the
    other members' charges do not move.

    Their open absences are closed (somebody not on the roster cannot
    be away), and the drop-in signups that joining the roster had
    cancelled are put back, so an add-then-remove does not erase a
    night they played.
    """
    # Locked like add_member: removing somebody offers their places to the
    # queue, and two overlapping removals must not promote the same person
    # twice.
    season = (
        db.query(SeasonRow).filter(SeasonRow.id == season_id).with_for_update().first()
    )
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
    away_from = _close_absences_of_former_member(db, season, player_id)
    # Before the fee sync, so the restored charges are already in the
    # ledger.
    _restore_absorbed_drop_ins(db, season, player_id, away_from)
    # After the restore, so a game this player is back on is still seen as
    # full, and before the fee sync, so the promoted drop-ins' charges are
    # in the ledger it reads. Each promoted person is told their night by
    # the deadline job.
    _offer_freed_slots_to_the_queue(db, season)
    _sync_member_season_fee_ledger(db, season, player_id, is_member=False)
    _audit(db, current_player, "roster_removed", season.club_id, subject_id=player_id)
    db.commit()


@router.get("/seasons/{season_id}/join-pool", response_model=list[ClubMemberOut])
def list_join_pool(
    season_id: int,
    db: Session = Depends(get_db),
    current_player: PlayerRow = Depends(get_current_player),
) -> list[ClubMemberOut]:
    """Club members not yet on this season's roster: the candidates the
    organizer can add with POST /seasons/{id}/members, whether they
    joined through LINE or were typed in. Organizer only. Returns
    memberships so the list shows who has asked to be a fixed member.
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
            ClubMemberRow.status == "active",
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
