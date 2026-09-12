"""Seasons: creating one, pricing it, its roster, and settling it."""

from collections import defaultdict

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    status,
)
from sqlalchemy import and_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from volleyflow.api.conversion import (
    season_from_rows,
)
from volleyflow.api.dependencies import get_db
from volleyflow.api.routes._attendance import (
    _absorb_drop_ins_into_membership,
    _close_absences_of_former_member,
    _delete_season_rows,
    _expected_on_court,
    _games_with_no_room,
    _is_signed_up,
    _offer_freed_slots_to_the_queue,
    _restore_absorbed_drop_ins,
    _restore_retired_absences,
    _retired_absence_game_ids,
    _within_change_deadline,
)
from volleyflow.api.routes._money import (
    _gather_member_settlements,
    _member_settlement_out,
    _sync_season_fee_ledger,
)
from volleyflow.api.routes._people import (
    _gender,
    _get_club_or_404,
    _get_or_create_player,
    _now,
    _require_club_access,
    _require_organizer,
    get_current_player,
)
from volleyflow.api.schemas import (
    AbsenceDetailOut,
    ClubMemberOut,
    DropInDetailOut,
    DropInSummary,
    GameDetailOut,
    GameOut,
    Gender,
    MemberAdd,
    MemberOut,
    SeasonCreate,
    SeasonDetailOut,
    SeasonOut,
    SeasonSettleOut,
    SeasonSummaryOut,
    SeasonUpdate,
    SettlementOut,
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
from volleyflow.pricing import share_per_game
from volleyflow.settlement import (
    season_shares,
)

router = APIRouter()


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

    # A fixed member holds a slot for the whole season (CLAUDE.md 2.3), so
    # the roster this season starts with can never be larger than its own
    # capacity — the same rule add_member enforces for a roster change
    # mid-season. This was the one way left to build an over-capacity
    # season, found by tests/api/test_fuzz.py once the mid-season path was
    # closed.
    if len(payload.member_names) > payload.capacity:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"{len(payload.member_names)} fixed members is more than the "
            f"capacity of {payload.capacity}",
        )

    season = SeasonRow(
        club_id=club_id,
        total_venue_cost=payload.total_venue_cost,
        ac_surcharge=payload.ac_surcharge,
        capacity=payload.capacity,
        minimum_roster=payload.minimum_roster,
        game_start_time=payload.game_start_time,
        game_end_time=payload.game_end_time,
        location=payload.location,
        change_deadline_days=payload.change_deadline_days,
    )
    db.add(season)
    db.flush()

    # Which nights are forecast to need the air conditioning. A forecast
    # only — each game can be corrected on the evening itself, which is
    # when anyone actually knows.
    cooled = set(payload.air_conditioned_dates)
    games = [
        GameRow(season_id=season.id, date=d, air_conditioned=d in cooled)
        for d in payload.game_dates
    ]
    db.add_all(games)

    # The same person named twice is one member, not two rows and a
    # crash: two names that resolve to one player (the list typed twice,
    # or two spellings the club already maps to one person) would
    # otherwise build two identical season_members rows and fail on the
    # primary key. Nothing about the season changes — a roster is a set.
    member_ids = []
    for name in payload.member_names:
        player = _get_or_create_player(db, club_id, name)
        if player.id in member_ids:
            continue
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
    games = (
        db.query(GameRow)
        .filter(GameRow.season_id == season.id)
        .order_by(GameRow.date)
        .all()
    )
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
    # All three, not just the venue cost: capacity is the other half of
    # share_per_game's denominator (CLAUDE.md 2.4), so moving any of them
    # would re-price a season whose ledger is already closed.
    priced = {"total_venue_cost", "ac_surcharge", "capacity"} & updates.keys()
    if priced and season.settled_at is not None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Season is already settled — venue cost can't change now",
        )

    # Lowering capacity below what's already on court would be the same
    # overfill add_member refuses, just reached from the other side — a
    # fixed member is expected at every game already, so the roster size
    # and every game's expected attendance are both floors on how far
    # capacity can drop.
    if "capacity" in updates and updates["capacity"] < season.capacity:
        new_capacity = updates["capacity"]
        roster_size = (
            db.query(SeasonMemberRow)
            .filter(SeasonMemberRow.season_id == season.id)
            .count()
        )
        games = db.query(GameRow).filter(GameRow.season_id == season.id).all()
        busiest = max((_expected_on_court(db, g, season) for g in games), default=0)
        floor = max(roster_size, busiest)
        if new_capacity < floor:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"Can't lower capacity to {new_capacity} — {floor} people "
                "are already on the roster or on a game's court",
            )

    for field, value in updates.items():
        setattr(season, field, value)

    if priced:
        db.flush()
        _sync_season_fee_ledger(db, season)

    db.commit()
    db.refresh(season)
    return _season_out(db, season)


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

    Locks the season row for the rest of the transaction, same reasoning
    as _get_game_or_404 and found the same way — by two requests racing
    for real. This route reads "is this person already a member" and
    "is there room", then writes based on those reads. Two overlapping
    calls for the same name each read no and each wrote: with the name
    already known to the club, that was a duplicate key and a 500; with
    a brand new name, far worse and completely silent — two Player rows
    for one person, both on the roster, both charged a season fee. The
    roster screen produces the overlap by itself, since adding somebody
    reloads the whole roster and a second tap lands on the redrawn
    button while the first request is still in the air.
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
    # the capacity-based split (CLAUDE.md 2.4) the season fee assumes
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

    # And every game needs a free slot right now as well, since a drop-in
    # may be standing in one that the new member would claim. Two kinds of
    # game don't count, because on both of them this player adds nobody to
    # the court:
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
    away_again = {
        game_id for game_id in _retired_absence_game_ids(db, season, player.id)
    }
    full = [
        game
        for game in _games_with_no_room(db, season)
        if not _is_signed_up(db, game, player.id) and game.id not in away_again
    ]
    if full:
        dates = ", ".join(str(game.date) for game in full[:3])
        more = f" (and {len(full) - 3} more)" if len(full) > 3 else ""
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"No room for another member: {dates}{more} "
            f"already {season.capacity} on court. Cancel a signup on "
            "those games, or raise the capacity first.",
        )

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
        # It takes two overlapping requests, which is exactly what the
        # roster screen produces: adding somebody reloads the whole
        # roster, the reload redraws the buttons, and a second tap lands
        # on the redrawn one while the first request is still in the air.
        # Found by tests/visual/smoke.js pressing every button on that
        # page (2026-09-12) — reported three times before that as an
        # unexplained 500.
        db.rollback()
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "Already a member of this season"
        ) from e
    # Before the fee sync, so the season's charges are worked out with
    # this member's leave already back where it was.
    _restore_retired_absences(db, season, player.id)
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
    """Same retroactive-share caveat as adding one. Their season-fee
    charge is reversed to zero and every remaining member's charge is
    corrected for the new, higher share — see _sync_season_fee_ledger.

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
    away_from = _close_absences_of_former_member(db, season, player_id)
    # Before the fee sync, so the restored drop-in charges are already
    # in the ledger when it works out what everyone owes.
    _restore_absorbed_drop_ins(db, season, player_id, away_from)
    # After the restore, so a game this player is back on as a drop-in is
    # correctly seen as still full and nobody is promoted into a place
    # that never opened. Before the fee sync, for the same reason the
    # restore is: the promoted drop-ins' charges belong in the ledger the
    # sync then reads.
    _offer_freed_slots_to_the_queue(db, season)
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
        # Members only. "Away" is a statement about somebody expected
        # here, so a leave record left behind by a player since taken off
        # the roster is not one — and printing it listed a name under
        # 請假 that is not on the roster above it. remove_member closes
        # these now; this covers the ones written before it did.
        .join(
            SeasonMemberRow,
            and_(
                SeasonMemberRow.player_id == AbsenceRow.player_id,
                SeasonMemberRow.season_id == season_row.id,
            ),
        )
        .filter(AbsenceRow.game_id.in_(game_ids), AbsenceRow.cancelled_at.is_(None))
        .order_by(AbsenceRow.recorded_at)
        .all()
    ):
        absences_by_game[absence.game_id].append((absence.id, player.name))

    # (drop_in_id, player_id, name, gender, covers_absence_id, brought_by)
    drop_ins_by_game: dict[
        int, list[tuple[int, int, str, Gender | None, int | None, int | None]]
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
                drop_in.brought_by_player_id,
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

        # Two different questions, deliberately answered separately.
        #
        # "Who did this member arrange to stand in for them?" has an
        # answer only when they actually arranged somebody
        # (covers_absence_id). "Does this absence get its money back?" is
        # answered by anybody filling the slot, matched FIFO — the rule in
        # settlement.covered_absences.
        #
        # These used to be one map, so a person who signed themselves up
        # off the waitlist was shown as a named member's 代打, and that
        # member was shown as having arranged them. Neither had agreed to
        # anything. Reported from real use on 2026-09-10.
        absence_name_by_id = dict(absences_list)
        arranged_for_absence: dict[str, str] = {}
        arranged_by_drop_in: dict[int, str] = {}
        claimed_absence_ids: set[int] = set()
        for entry in drop_ins:
            drop_in_id, _player_id, name, _drop_in_gender, covers_absence_id, _by = (
                entry
            )
            if covers_absence_id in absence_name_by_id:
                absence_name = absence_name_by_id[covers_absence_id]
                arranged_for_absence[absence_name] = name
                arranged_by_drop_in[drop_in_id] = absence_name
                claimed_absence_ids.add(covers_absence_id)

        fifo_absences = [
            (aid, name) for aid, name in absences_list if aid not in claimed_absence_ids
        ]
        fifo_drop_ins = [
            name
            for _drop_in_id, _player_id, name, _gender, covers, _by in drop_ins
            if covers is None
        ]
        # Who is standing in the slot — which is also what decides the
        # refund. Named, but not "covered by": nobody asked them.
        filled_by: dict[int, str] = {
            aid: arranged_for_absence[absence_name_by_id[aid]]
            for aid in claimed_absence_ids
        }
        for i, (aid, _absence_name) in enumerate(fifo_absences):
            if i < len(fifo_drop_ins):
                filled_by[aid] = fifo_drop_ins[i]

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
                        id=aid,
                        player_name=name,
                        covered_by=arranged_for_absence.get(name),
                        filled_by=filled_by.get(aid),
                    )
                    for aid, name in absences_list
                ],
                confirmed_drop_ins=[
                    DropInDetailOut(
                        id=drop_in_id,
                        player_id=player_id,
                        player_name=name,
                        gender=gender,
                        covering=arranged_by_drop_in.get(drop_in_id),
                        # Themselves, or a guest they brought. The screen
                        # uses this to decide whose signup it may offer to
                        # cancel; the server checks it again on the way in.
                        signed_up_by_me=(
                            player_id == current_player.id
                            or brought_by == current_player.id
                        ),
                    )
                    for (
                        drop_in_id,
                        player_id,
                        name,
                        gender,
                        _covers,
                        brought_by,
                    ) in drop_ins
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
        #
        # Divided by capacity, not by the roster. This call site was
        # missed when the rule changed on 2026-09-10 (CLAUDE.md 2.4), so
        # the screens quoted a price the ledger never charged whenever the
        # roster wasn't exactly full — and an empty roster divided by zero
        # and returned a 500, which is how tests/api/test_fuzz.py found it.
        share_per_game=share_per_game(
            season_row.total_venue_cost
            - season_row.ac_surcharge * sum(1 for g in game_rows if g.air_conditioned),
            len(game_rows),
            season_row.capacity,
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
