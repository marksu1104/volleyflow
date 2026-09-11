"""Pydantic request/response models — the API's wire format.

Kept separate from db/models.py on purpose: what a client sends and
receives isn't the same shape as a database row (a request has no id
yet; a response doesn't need every internal column).
"""

from datetime import date, datetime, time
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from volleyflow.ledger import EntryType
from volleyflow.schedule import GameStatus

Gender = Literal["male", "female"]


class ClubCreate(BaseModel):
    name: str
    """Whoever creates the club becomes its organizer — see
    routes.create_club. Who "whoever" is comes from the caller's
    verified LINE identity (routes._get_current_player), not a
    client-supplied id: letting the body name an arbitrary player_id
    would let anyone make anyone else the organizer of a new club."""


class ClubOut(BaseModel):
    id: int
    name: str
    role: str | None = None
    """The caller's role in this club — "organizer" or "member".

    Carried so the management pages can list only the clubs the caller
    actually organizes. Without it their club picker offered every club
    they merely belong to, which put a management screen in front of an
    ordinary member. None on GET /clubs/{id}, the invite-link name
    lookup, where the caller has no role yet by definition.
    """


class InviteOut(BaseModel):
    """The join-link token for a club, and what it resolves to.

    `token` is only present on GET /clubs/{id}/invite (the organizer
    generating a link to share); GET /invites/{token} (a visitor opening
    that link) only needs to answer "whose invite is this", not echo the
    token back. See routes.get_club_invite and routes.resolve_invite.
    """

    club_id: int
    club_name: str
    token: str | None = None


class MyClubOut(BaseModel):
    id: int
    name: str
    role: str
    """"organizer" or "member" in this specific club."""
    wants_fixed_membership: bool | None = None
    """See ClubMemberRow.wants_fixed_membership — null means this person
    was never asked, which is what the member page uses to decide whether
    to ask them."""


class MembershipIntent(BaseModel):
    wants_fixed_membership: bool


class ClubMemberOut(BaseModel):
    id: int
    name: str
    gender: Gender | None = None
    avatar_url: str | None = None
    linked: bool
    """See MemberOut.linked."""
    role: str
    wants_fixed_membership: bool | None = None
    """See ClubMemberRow.wants_fixed_membership. Shown to the organizer so
    the join pool distinguishes "waiting to be put on the roster" from
    "here for the odd game"."""
    """"organizer" or "member" — see ClubMemberRow."""


class SeasonCreate(BaseModel):
    total_venue_cost: Decimal
    ac_surcharge: Decimal = Decimal("0")
    """What one game's air conditioning adds, on the same terms as
    total_venue_cost — both are what the club actually pays, so a
    discounted season takes the discounted surcharge. Zero means the
    venue bundles it, and every game then costs the same."""
    air_conditioned_dates: list[date] = Field(default_factory=list)
    """Which of `game_dates` are forecast to need the air conditioning.
    A forecast only: each game can be corrected on the day — see
    routes.set_game_air_conditioning."""
    game_dates: list[date] = Field(min_length=1)
    member_names: list[str] = Field(min_length=1)
    capacity: int = 18
    minimum_roster: int = 12
    game_start_time: time | None = None
    game_end_time: time | None = None
    location: str | None = None
    change_deadline_days: int | None = None


class SeasonUpdate(BaseModel):
    """A partial update — only fields actually present in the request
    body are touched (see routes.update_season's use of
    `exclude_unset`), so e.g. clearing `location` back to null and
    leaving it alone are distinguishable requests.
    """

    total_venue_cost: Decimal | None = None
    ac_surcharge: Decimal | None = None
    capacity: int | None = None
    minimum_roster: int | None = None
    game_start_time: time | None = None
    game_end_time: time | None = None
    location: str | None = None
    change_deadline_days: int | None = None


class MemberAdd(BaseModel):
    player_name: str
    gender: Gender | None = None
    """Only used when this name is new to the club. Someone added by name
    has no account to set it from themselves, and the roster's male/female
    count needs it — so it's offered at the one moment the organizer is
    already typing them in."""


class AirConditioningUpdate(BaseModel):
    air_conditioned: bool


class GameOut(BaseModel):
    id: int
    date: date
    status: GameStatus


class SeasonOut(BaseModel):
    id: int
    total_venue_cost: Decimal
    capacity: int
    minimum_roster: int
    game_start_time: time | None
    game_end_time: time | None
    location: str | None
    change_deadline_days: int | None
    games: list[GameOut]
    member_ids: list[int]


class SeasonSummaryOut(BaseModel):
    """One row in the season picker — enough to label a season without
    fetching its full detail (dates, not an opaque id)."""

    id: int
    first_game_date: date
    last_game_date: date
    total_games: int
    member_count: int
    settled: bool


class AbsenceCreate(BaseModel):
    player_name: str
    game_id: int


class AbsenceOut(BaseModel):
    id: int
    player_id: int
    game_id: int
    recorded_at: datetime
    promoted_from_waitlist: int | None = None
    """Player id pulled off the waitlist to fill this slot, if any."""


class AbsenceCancelOut(BaseModel):
    id: int
    cancelled_at: datetime
    released_player_id: int | None = None
    """Who went back to the queue because this member is playing after
    all. The slot only opened because it was released, so taking that
    back closes it — but the screen has to name the person, or they
    disappear off the roster with no explanation."""


class SubstituteCreate(BaseModel):
    player_name: str
    gender: Gender | None = None


class DropInCreate(BaseModel):
    player_name: str
    game_id: int
    # Only ever fills a gender in, never overwrites one — a guest being
    # brought by a member is usually a brand new name, and the roster
    # shows 男/女 because team balance is decided off it.
    gender: Gender | None = None


class DropInOut(BaseModel):
    status: Literal["confirmed", "waitlisted"]
    id: int
    player_id: int
    game_id: int
    displaced_player_id: int | None = None
    """Who went back to the waitlist to make room for a named substitute.

    Only ever set by PUT /absences/{id}/substitute: recording an absence
    hands the empty slot straight to the queue, so naming the person you
    actually wanted usually means somebody has to step back out. The
    screen has to be able to say who, or a player simply vanishes off
    the roster with no explanation."""


class DropInBatchEntry(BaseModel):
    """One person in a "+1, and I'm bringing two friends" signup.

    `player_id` is what decides identity, and it is deliberately never
    inferred from the name. Two real people called 小明 must both be
    able to play, so a bare name always means *a new person*; reusing an
    existing one is something the caller has to say explicitly, after
    the app has offered it. Guessing the other way round would put one
    person's fee on another person's ledger, which is not recoverable —
    a duplicate row merely looks untidy.
    """

    player_name: str
    gender: Gender | None = None
    player_id: int | None = None

    @model_validator(mode="after")
    def _gender_required_for_new_people(self) -> "DropInBatchEntry":
        # The roster's 男/女 tags are what the team is picked from, so a
        # blank is a hole someone has to chase later. An existing player
        # is exempt: theirs is already on file.
        if self.player_id is None and self.gender is None:
            raise ValueError("gender is required when signing up a new person")
        if not self.player_name.strip():
            raise ValueError("player_name cannot be blank")
        return self


class DropInBatchCreate(BaseModel):
    """Signing several people up is one request, not several.

    Capacity has to be decided for the whole group at once, under the
    same row lock: three sequential requests can interleave with someone
    else's and let the game go over capacity, and they can also half-
    succeed, which is the worst state to leave money in.
    """

    people: list[DropInBatchEntry] = Field(min_length=1, max_length=10)


class DropInBatchOut(BaseModel):
    results: list[DropInOut]


class WaitlistCancelOut(BaseModel):
    """A queue place is deleted outright, not marked cancelled — it
    carries no money and no history worth keeping, so there is no
    cancelled_at to report."""

    id: int
    player_id: int
    game_id: int


class WaitlistPromote(BaseModel):
    """The organizer putting a specific queued person on the court.

    Automatic promotion is strictly first-queued-first (CLAUDE.md 2.3),
    and stays that way. This is the manual override, because the person
    at the front of the queue is often the one who can't make it
    tonight — the organizer knows that and the queue doesn't.

    `replacing_drop_in_id` is what makes the choice possible without
    breaking the capacity cap. Once a game is full the only way to bring
    somebody in is to take somebody out, and doing that as two requests
    means the cancellation's own automatic promotion fills the slot with
    the wrong person first: each of those is a real charge and a real
    refund on somebody's ledger. One request swaps them directly, so the
    only money that moves belongs to the two people actually swapping.
    """

    replacing_drop_in_id: int | None = None


class WaitlistPromoteOut(BaseModel):
    player_id: int
    """Who is now on the court."""
    game_id: int
    drop_in_id: int
    replaced_player_id: int | None = None
    """Who came off to make room, when this was a swap."""


class DropInCancelOut(BaseModel):
    id: int
    cancelled_at: datetime
    promoted_from_waitlist: int | None = None
    """Player id pulled off the waitlist to fill the newly-open slot, if any."""


class MemberSettlementOut(BaseModel):
    player_id: int
    player_name: str
    season_fee: Decimal
    refund: Decimal
    net: Decimal
    """Positive: the organizer owes the member. Negative: the member owes."""


class SettlementOut(BaseModel):
    season_id: int
    members: list[MemberSettlementOut]


class MemberOut(BaseModel):
    id: int
    name: str
    gender: Gender | None = None
    avatar_url: str | None = None
    linked: bool
    """Whether this player has claimed a LINE identity. False means the
    organizer typed their name in and they've never opened the app — so
    they can't record their own absence, sign themselves up, or set their
    own gender, and somebody has to do it for them. Worth showing on a
    roster rather than leaving the organizer to guess."""


class GenderUpdate(BaseModel):
    gender: Gender


class NameUpdate(BaseModel):
    name: str


class PlayerLink(BaseModel):
    line_player_id: int
    """The Player row created when this person opened the app with LINE,
    to be folded into the roster entry the organizer typed in earlier —
    see routes.link_player."""


class GameCancel(BaseModel):
    refunded: bool
    """True: CANCELLED_REFUNDED — the venue returned this game's cost, so
    billable_games drops by one and every current member is credited
    share_per_game back. False: CANCELLED_UNREFUNDED — the venue cost
    was already paid regardless, so nobody's charge changes. See
    docs/billing-rules.md "Game cancellation"."""


class PlayerIdentify(BaseModel):
    """What the LIFF page sends right after LIFF resolves. id_token
    (from liff.getIDToken()) is verified server-side — see
    api.auth.verify_id_token — and is the only source of line_user_id;
    display_name/picture_url stay client-reported, since spoofing your
    own displayed name isn't an identity problem the way spoofing whose
    account you become would be.
    """

    id_token: str
    display_name: str
    picture_url: str | None = None


class PlayerIdentifyOut(BaseModel):
    id: int
    name: str
    """May differ from the display_name that was sent, if that name
    collided with a different existing Player — see
    routes._unique_display_name."""
    avatar_url: str | None = None
    gender: Gender | None = None


class GuestOut(BaseModel):
    """Somebody the caller has brought to this club before.

    `times` and `last_played` are there to tell two people with the same
    name apart — the whole reason this list exists is that retyping a
    name creates a second person, and a picker that shows "王小明 · 打過
    6 次 · 上次 9/15" beside "王小明 · 打過 1 次 · 上次 7/2" makes the
    right one obvious.
    """

    id: int
    name: str
    gender: Gender | None = None
    times: int
    last_played: date


class DropInSummary(BaseModel):
    id: int
    """The drop-in or waitlist entry id — pass this to the cancel endpoint."""
    player_name: str
    gender: Gender | None = None


class AbsenceDetailOut(BaseModel):
    id: int
    """Pass this to /absences/{id}/cancel or /absences/{id}/substitute."""
    player_name: str
    covered_by: str | None
    """The 代打 this member personally arranged, by name — set only when
    somebody used /absences/{id}/substitute. Never filled in by the FIFO
    match: that is a billing fact, not a person's arrangement, and
    presenting it as one told a member that a stranger who happened to
    sign up was "their" substitute. See `refunded`."""
    filled_by: str | None = None
    """Whoever is standing in this slot, by name — the arranged 代打 when
    there is one, otherwise whichever 臨打 the FIFO match landed on.

    Having a name here is what makes the share come back (settlement.py),
    so this doubles as "is this refunded". Reported separately from
    `covered_by` on purpose: both name a person, but only `covered_by`
    means that person was asked."""


class DropInDetailOut(BaseModel):
    id: int
    player_id: int
    player_name: str
    gender: Gender | None = None
    covering: str | None
    """The absent member this drop-in was personally named to stand in
    for — only for an explicit 代打. Somebody who signed themselves up is
    a 臨打 and covers nobody in particular, even when their fee is what
    refunds an absence."""
    signed_up_by_me: bool = False
    """Whether the caller is the one who put this person on the list —
    themselves, or a guest they brought. What the screen uses to decide
    whether to offer them a cancel button; the server checks the same
    thing again before allowing it."""


class GameDetailOut(BaseModel):
    id: int
    date: date
    status: GameStatus
    locked: bool
    """Whether *the caller* can still change this game: past the season's
    change deadline, absences, signups and their cancellations are all
    rejected. False for the club's organizer whatever the date, since the
    deadline exists to stop the roster shifting under them and they're
    the one who has to record what actually happened — so this is
    answered per caller, not per game."""
    air_conditioned: bool
    """Whether the air conditioning ran (or is forecast to). Set when the
    season is created and corrected on the day — see
    routes.set_game_air_conditioning."""
    share: Decimal
    """What this particular game costs one person. Differs between games
    once air conditioning is priced, which is why an absence refund and
    a drop-in's charge both key off the game rather than the season."""
    absences: list[AbsenceDetailOut]
    confirmed_drop_ins: list[DropInDetailOut]
    waitlist_entries: list[DropInSummary]


class SeasonDetailOut(BaseModel):
    id: int
    total_venue_cost: Decimal
    capacity: int
    minimum_roster: int
    game_start_time: time | None
    game_end_time: time | None
    location: str | None
    change_deadline_days: int | None
    share_per_game: Decimal
    """What one game costs one person, for a game with no air
    conditioning. Computed here, not on the frontend: rounding happens
    in exactly one place (pricing.shares_by_game).

    No longer the only figure in play — a cooled game costs
    `ac_surcharge / member_count` more, and each game carries its own
    `share` below. This stays as the headline number a season is
    described by."""
    ac_surcharge: Decimal
    """What one game's air conditioning adds to the venue bill; 0 when
    the venue bundles it or the club doesn't use it."""
    settled_at: datetime | None
    members: list[MemberOut]
    games: list[GameDetailOut]


class SeasonSettleOut(BaseModel):
    season_id: int
    settled_at: datetime
    members: list[MemberSettlementOut]


class PaymentCreate(BaseModel):
    amount: Decimal
    """Signed from the player's point of view: positive means the player
    paid the organizer, negative means the organizer paid the player."""
    season_id: int | None = None
    note: str | None = None
    client_token: str | None = None
    """Generated once per intended payment by the caller. Sending the
    same token again returns the entry already recorded instead of
    recording a second one — so a double tap, or a retry after a request
    that timed out on a bad connection, can't book the money twice. See
    routes.record_payment."""


class LedgerEntryOut(BaseModel):
    id: int
    entry_type: EntryType
    amount: Decimal
    recorded_at: datetime
    season_id: int | None
    note: str | None


class ProblemReport(BaseModel):
    message: str
    """What went wrong, in the reporter's own words."""
    page: str | None = None
    """Which screen they were on. Sent by the page rather than typed,
    because "it broke" is only actionable with the where."""
    user_agent: str | None = None
    club_id: int | None = None
    screenshot: str | None = None
    """A data URL (`data:image/jpeg;base64,...`). The page shrinks the
    picture before sending — a raw phone screenshot is several megabytes,
    and a bad connection is exactly the situation someone reports from."""


class PlayerBalanceOut(BaseModel):
    """One player's money in one club, summed three ways at once.

    The ledger page needs all three per person, and used to fetch a whole
    entry history per member to add them up in the browser — one request
    each, forty on a page load for a normal-sized club. The database can
    do the same arithmetic for everyone in a single pass.
    """

    player_id: int
    balance: Decimal
    """Everything this player has ever been charged or credited in this
    club. Positive: the organizer owes them."""
    season_total: Decimal
    """The part of that belonging to the season asked about — so the rest
    of the balance is what carried in from elsewhere."""
    season_fee_charged: Decimal
    """Just the season-fee entries for that season, before any payment,
    which is what "本季季費" means on screen."""
    brought_by: str | None = None
    """Who signed this player up, when they're a guest somebody brought.

    Only meaningful on the money screen: the fee is on this player's
    ledger but a guest has no account to pay from, so this names the
    member who actually hands over the cash. None for anyone who signed
    themselves up.
    """


class PlayerLedgerOut(BaseModel):
    player_id: int
    player_name: str
    balance: Decimal
    """Positive: the organizer owes the player. Negative: the player owes."""
    entries: list[LedgerEntryOut]
