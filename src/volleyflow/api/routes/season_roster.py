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
    """Add one fixed member and charge only that person's season fee.

    A share is divided by the season's capacity, not its current roster,
    so this never re-prices the existing members. On a full upcoming game,
    fixed membership takes priority and the latest confirmed drop-in goes
    back to the waitlist with their charge reversed. Blocked once settled:
    the ledger is already final. See docs/billing-rules.md, "Who the cost
    is split between".

    Locks the season row for the rest of the transaction, same reasoning
    as _get_game_or_404 and found the same way — by two requests racing
    for real. This route reads "is this person already a member" and
    "is there room", then writes based on those reads. Two overlapping
    calls for the same name each read no and each wrote: with the name
    already known to the club, that was a duplicate key and a 500; with
    a brand new name, far worse and completely silent — two Player rows
    for one person, both on the roster, both charged a season fee. An
    older roster screen could produce the overlap by redrawing the same
    button while the first request was still in the air; the API still
    has to be safe for retries and other clients.
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
    # Never overwrites: a returning player's own setting is theirs, and
    # this argument is just what the organizer happened to type today.
    if payload.gender is not None and player.gender is None:
        player.gender = payload.gender
    existing = db.get(SeasonMemberRow, {"season_id": season_id, "player_id": player.id})
    if existing is not None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "Already a member of this season"
        )

    # A fixed member holds a slot for the whole season, so the roster can
    # never be larger than the number of slots — a nineteenth member of an
    # eighteen-slot season is somebody with nowhere to stand, and under
    # the capacity-based split the season fee assumes
    # exactly one payer per slot.
    #
    # Checked separately from the per-game count below, and it has to be:
    # an absence is temporary, so a game whose members are mostly away
    # looks like it has room when it does not. A random sweep walked
    # straight through that — add a member while four people were away,
    # then have all four cancel, and seven people were on a court for six.
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

    # A full game needs one slot handed back before this member is added.
    # On a future date the latest drop-in yields that slot; on a past date
    # the new member is recorded away so history does not gain a body who
    # was never there. Two kinds of game need neither treatment, because
    # on both of them this player adds nobody to the court:
    #
    #  - one they already hold a signup for. Those are absorbed into the
    #    membership below rather than sitting beside it.
    #  - one they are about to be away from again, because a removal
    #    closed their leave and rejoining puts it back (see
    #    _restore_retired_absences). This is the case that made removing
    #    a member a one-way door: their substitute keeps the slot, so the
    #    game sits at capacity while the roster is one short, and putting
    #    them back was refused over a court they were never going to
    #    stand on. Reported from real use on 2026-09-12 — 18 members,
    #    remove one, 17 on the roster and still "raise the capacity to
    #    19" when adding them back.
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

    # A correction made after a game has happened must not invent a person
    # on that night's court. If the game was already full, keep its actual
    # attendance unchanged by recording the newly-added fixed member as
    # away. Their full-season charge is still correct, and the ordinary
    # coverage rules decide later whether the drop-in earns them a refund.
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
        # Two of these arrived close enough together that both got past
        # the "already a member" read above; the primary key caught the
        # loser. Which is the answer the loser wanted anyway — they are a
        # member of this season — so it is the same 400 that check gives,
        # not a 500.
        #
        # It takes two overlapping requests. An older roster screen
        # produced that overlap by reloading and redrawing the button
        # while the first write was still in flight. Found by
        # tests/visual/smoke.js pressing every button on that page
        # (2026-09-12) — reported three times before that as an
        # unexplained 500.
        db.rollback()
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "Already a member of this season"
        ) from e
    # Before the fee sync, so the season's charges are worked out with
    # this member's leave already back where it was.
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
    """Remove one fixed member and reverse only that person's season fee.

    Capacity stays fixed, so the remaining members' season charges do not
    move. See docs/billing-rules.md, "Who the cost is split between".

    Their outstanding absences are closed. Leaving them open was fine for
    settlement, which only ever walks the current member list, and wrong
    everywhere else: "not coming to this game" says nothing about
    somebody who is not expected at it in the first place. Left live they
    were counted as a free seat by the capacity check, matched against
    drop-ins by the refund rule, and listed on the game sheet as away —
    a ghost of somebody no longer in the season. A random sweep
    (tests/api/test_fuzz.py) found two of those three.

    Not restored if the person is added back, unlike drop-ins below: a
    drop-in is a night somebody really played and really owes for, while
    an absence is a statement about a roster they had left. Re-recording
    one is two taps for the organizer who just re-added them.

    Drop-ins are not left alone either, which is the fix for a bug
    reported on 2026-09-10 — adding somebody to the roster cancels the
    signups they had already made so the same night isn't billed twice,
    and without putting those back an add-then-remove erased a night
    they had actually played, and the fee owed for it with it.
    """
    # Locked for the same reason add_member locks it, and found the same
    # way: tests/visual/smoke.js pressing every button on the roster page
    # (2026-09-16) answered 500 here. Taking somebody off frees their
    # place at every future game and offers it to the queue, and two of
    # those overlapping both read "who is first in line" and both wrote
    # that person in — one person, two slots at one game, which
    # uq_drop_ins_active_player_game refuses. The roster screen produces
    # the overlap by itself: a tap reloads the list, the reload redraws
    # the buttons, and the next tap lands while the first request is
    # still in the air.
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
    # Before the fee sync, so the restored drop-in charges are already
    # in the ledger when it works out what everyone owes.
    _restore_absorbed_drop_ins(db, season, player_id, away_from)
    # After the restore, so a game this player is back on as a drop-in is
    # correctly seen as still full and nobody is promoted into a place
    # that never opened. Before the fee sync, for the same reason the
    # restore is: the promoted drop-ins' charges belong in the ledger the
    # sync then reads.
    # One place freed at every game they were expected at, so this can
    # promote several people into several nights; each is told their own
    # by the deadline job (reminders.send_promotion_notices).
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
