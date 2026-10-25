"""Pydantic request and response models: the API's wire format.

Separate from db/models.py because what a client sends and receives is
not the shape of a database row.
"""

from datetime import date, datetime, time
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints, model_validator

from volleyflow.ledger import EntryType
from volleyflow.schedule import GameStatus

Gender = Literal["male", "female"]

CLUB_NAME_MAX = 20
# Long names wrapped the club chip onto three lines on a phone.
ClubName = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=CLUB_NAME_MAX),
]


class ClubCreate(BaseModel):
    name: ClubName
    """The caller becomes the club's organizer, identified by their
    verified LINE token, never by an id in the body.
    """


class ClubUpdate(BaseModel):
    name: ClubName


class JoinApproval(BaseModel):
    as_fixed: bool
    """Let in as a fixed member (put on a season's roster next) or for single games."""


class ClubJoin(BaseModel):
    invite: str
    """The token from the club's invite link. Required, so nobody can
    join clubs by trying ids.
    """
    wants_fixed_membership: bool | None = None
    """What they ask to be: a fixed member, or here for single games."""


class ClubGuestAdd(BaseModel):
    """An accountless person the organizer wants to keep in the club.
    Names no season, so creating them never charges a season fee.
    """

    name: str
    gender: Gender | None = None


class ClubOut(BaseModel):
    id: int
    name: str
    developer_view: bool = False
    """True when the caller sees this club only because they are the
    developer; the management pages say so.
    """
    role: str | None = None
    """The caller's role in this club, "organizer" or "member", so the
    management pages list only clubs they organize. None on the invite
    lookup, where the caller has no role yet.
    """


class InviteOut(BaseModel):
    """A club's invite token and the club it opens. `token` is only
    returned to the organizer generating the link (GET
    /clubs/{id}/invite).
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
    """See ClubMemberRow.wants_fixed_membership; null means they were
    never asked.
    """
    status: str = "active"
    """'pending' until the organizer approves them; see ClubMemberRow.status."""
    pending_count: int = 0
    """For an organizer: how many people are waiting to be approved."""
    balance: Decimal = Decimal(0)
    """This player's money in this club; positive means the organizer
    owes them. One figure per club and never a total, since balances
    do not move between clubs.
    """


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
    """"organizer" or "member" — see ClubMemberRow."""
    status: Literal["active", "pending"] = "active"
    """Pending members appear only when an organizer asks for them."""
    wants_fixed_membership: bool | None = None
    """See ClubMemberRow.wants_fixed_membership: tells "waiting for the
    roster" apart from "here for the odd game".
    """


class SeasonCreate(BaseModel):
    total_venue_cost: Decimal
    ac_surcharge: Decimal = Decimal("0")
    """What one game's air conditioning adds, on the same terms as
    total_venue_cost. Zero when the venue includes it.
    """
    air_conditioned_dates: list[date] = Field(default_factory=list)
    """Which of `game_dates` are forecast to need the air conditioning;
    each game can be corrected on the night.
    """
    game_dates: list[date] = Field(min_length=1)
    member_names: list[str] = Field(min_length=1)
    capacity: int = 18
    minimum_roster: int = 12
    game_start_time: time | None = None
    game_end_time: time | None = None
    location: str | None = None
    change_deadline_hours: int = Field(default=24, ge=0)


class SeasonUpdate(BaseModel):
    """A partial update: only fields present in the body are touched
    (`exclude_unset`), so clearing `location` and leaving it alone are
    different requests.
    """

    total_venue_cost: Decimal | None = None
    ac_surcharge: Decimal | None = None
    capacity: int | None = None
    minimum_roster: int | None = None
    game_start_time: time | None = None
    game_end_time: time | None = None
    location: str | None = None
    change_deadline_hours: int | None = Field(default=None, ge=0)


class MemberAdd(BaseModel):
    player_name: str
    gender: Gender | None = None
    """Used only when the name is new to the club, so the roster's
    male/female count has it.
    """


class AirConditioningUpdate(BaseModel):
    air_conditioned: bool


class AirConditionedDatesUpdate(BaseModel):
    dates: list[date] = Field(default_factory=list)
    """The games forecast to need the air conditioning; the whole set is
    replaced. Corrects the forecast only, so `total_venue_cost` stays
    the same (unlike routes.set_game_air_conditioning, for what
    actually ran).
    """


class AirConditionedPreviewGame(BaseModel):
    date: date
    air_conditioned: bool
    share_before: Decimal
    share_after: Decimal


class AirConditionedPreviewOut(BaseModel):
    """What a corrected forecast would cost: each game's share and a
    member's season fee, before and after. Nothing is written.
    """

    season_fee_before: Decimal
    season_fee_after: Decimal
    games: list[AirConditionedPreviewGame]


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
    change_deadline_hours: int
    games: list[GameOut]
    member_ids: list[int]


class SeasonSummaryOut(BaseModel):
    """One row in the season picker: enough to label a season without its
    detail.
    """

    id: int
    first_game_date: date
    last_game_date: date
    total_games: int
    member_count: int
    settled: bool


class AbsenceCreate(BaseModel):
    player_name: str
    game_id: int
    substitute: "SubstituteCreate | None" = None
    """A substitute named with the absence; the slot goes straight to
    them and is never offered to the queue.
    """


class AbsenceOut(BaseModel):
    id: int
    player_id: int
    game_id: int
    recorded_at: datetime
    promoted_from_waitlist: int | None = None
    """Player id pulled off the waitlist to fill this slot, if any."""
    substitute: "DropInOut | None" = None
    """The substitute's signup, when one was named with the absence."""


class AbsenceCancelOut(BaseModel):
    id: int
    cancelled_at: datetime
    released_player_id: int | None = None
    """Who went back to the queue because this member is playing after
    all, so the screen can name them.
    """


class SubstituteCreate(BaseModel):
    player_name: str
    gender: Gender | None = None


class DropInCreate(BaseModel):
    player_name: str
    game_id: int
    # Only ever fills a gender in, never overwrites one.
    gender: Gender | None = None


class DropInOut(BaseModel):
    status: Literal["confirmed", "waitlisted"]
    id: int
    player_id: int
    game_id: int
    displaced_player_id: int | None = None
    """Who went back to the queue to make room for a named substitute, so
    the screen can name them.
    """


class DropInBatchEntry(BaseModel):
    """One person in a group signup. Within a club a name is one person;
    two people who share a name are told apart by what is typed, e.g.
    小明（高）. `player_id` picks somebody explicitly.
    """

    player_name: str
    gender: Gender | None = None
    player_id: int | None = None

    @model_validator(mode="after")
    def _gender_required_for_new_people(self) -> "DropInBatchEntry":
        # New names need a gender for the roster's male/female count;
        # existing players already have one.
        if self.player_id is None and self.gender is None:
            raise ValueError("gender is required when signing up a new person")
        if not self.player_name.strip():
            raise ValueError("player_name cannot be blank")
        return self


class DropInBatchCreate(BaseModel):
    """Several people signed up in one request, so capacity is decided
    for the whole group under one lock and the group cannot
    half-succeed.
    """

    people: list[DropInBatchEntry] = Field(min_length=1, max_length=10)


class DropInBatchOut(BaseModel):
    results: list[DropInOut]


class WaitlistCancelOut(BaseModel):
    """A queue place given up. Queued signups were never charged, so no
    money moves.
    """

    id: int
    player_id: int
    game_id: int


class WaitlistPromote(BaseModel):
    """The organizer putting a specific queued person on court,
    overriding the queue's order.

    `replacing_drop_in_id` names who comes off a full game, so the
    swap is one request and only those two ledgers move.
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
    """Whether this player has a LINE account. False means somebody typed
    their name in, so others have to act for them.
    """
    fee_notice_sent_at: datetime | None = None
    """When this member was sent this season's 繳費通知, if they were."""


class DisplacedDropInOut(BaseModel):
    player_id: int
    player_name: str
    game_id: int
    game_date: date


class SeasonMemberAddOut(MemberOut):
    displaced_drop_ins: list[DisplacedDropInOut] = Field(default_factory=list)
    """Signups moved back to the queue because this fixed member took
    priority, so the organizer sees who moved.
    """


class GenderUpdate(BaseModel):
    gender: Gender


class NameUpdate(BaseModel):
    name: str


class PlayerLink(BaseModel):
    line_player_id: int
    """The Player row created when this person opened the app with LINE,
    to be folded into their roster entry; see routes.link_player.
    """


class GameCancel(BaseModel):
    refunded: bool
    """True: CANCELLED_REFUNDED, the venue returned this game's cost, so
    every member is credited that game's share. False:
    CANCELLED_UNREFUNDED, nobody's charge changes. See
    docs/billing-rules.md "Game cancellation".
    """


# `date` is also a field name on GameUpdate below; with a default, the field
# would shadow the type in the class body and break `date | None`.
_DateType = date


class GameUpdate(BaseModel):
    """A partial update to one game: only fields present in the body are
    touched, as with SeasonUpdate.

    A date change is a move, not a cancellation, so it moves no money;
    location and time are display-only.
    """

    date: _DateType | None = None
    """Where this game moves to."""
    location: str | None = None
    start_time: time | None = None
    end_time: time | None = None
    """This night's own venue and time; null puts it back on the
    season's.
    """
    venue_cost_delta: Decimal | None = None
    """What this night costs above (or below) a normal one. The only
    field here that moves money: it changes the season's total and
    re-prices every member. Null clears it. See docs/billing-rules.md
    "A different venue for one night".
    """


class PlayerIdentify(BaseModel):
    """What a LIFF page sends after LIFF starts. The ID token is verified
    with LINE and is the only source of the LINE user id; the display
    name and picture are as the client reports them.
    """

    id_token: str
    display_name: str
    picture_url: str | None = None


class PlayerIdentifyOut(BaseModel):
    id: int
    name: str
    """The player's name in the app. LINE supplies the first value; a
    name edited in the app wins afterwards.
    """
    avatar_url: str | None = None
    gender: Gender | None = None
    is_developer: bool = False
    """Whether to show the way into problem reports. The server checks
    again on every report request.
    """


class LineReachableOut(BaseModel):
    reachable: bool | None = None
    """Whether LINE would deliver a push: true if they added the Official
    Account, false if not, null if it could not be checked. The screen
    prompts only on a definite false.
    """


class GuestOut(BaseModel):
    """Somebody the caller has brought before. `times` and `last_played`
    tell two people with the same name apart.
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
    signed_up_by_me: bool = False
    """Themselves, or a guest they queued — whether they may take it back."""
    avatar_url: str | None = None
    """Their LINE profile picture, or None without a LINE account. Shown
    only to people in the same club.
    """
    brought_by_name: str | None = None
    """Who queued this person, when it was not themselves."""


class AbsenceDetailOut(BaseModel):
    id: int
    """Pass this to /absences/{id}/cancel or /absences/{id}/substitute."""
    player_name: str
    covered_by: str | None
    """The 代打 this member personally arranged, by name. Never set by the
    FIFO match, which is a billing fact, not an arrangement. See
    `filled_by`.
    """
    avatar_url: str | None = None
    """Their LINE profile picture, or None without a LINE account."""
    filled_by: str | None = None
    """Whoever is standing in this slot: the arranged 代打, else the
    drop-in the FIFO match landed on. A name here means the absence is
    refunded.
    """


class DropInDetailOut(BaseModel):
    id: int
    player_id: int
    player_name: str
    gender: Gender | None = None
    avatar_url: str | None = None
    """Their LINE profile picture, or None without a LINE account."""
    covering: str | None
    """The absent member this drop-in was named to stand in for; only for
    an explicit 代打.
    """
    signed_up_by_me: bool = False
    """Whether the caller put this person on the list (themselves or a
    guest they brought), so the screen knows whether to offer cancel.
    The server checks again.
    """
    brought_by_name: str | None = None
    """Who signed this person up, when it was not themselves."""


class GameDetailOut(BaseModel):
    id: int
    date: date
    status: GameStatus
    locked: bool
    """Whether the caller can no longer change this game. Never true for
    the club's organizer, so it is per caller, not per game.
    """
    past_deadline: bool = False
    """Whether the game is past its change deadline, whoever asks. The
    member screen locks on this, holding the organizer to the deadline
    there too.
    """
    air_conditioned: bool
    """Whether the air conditioning ran, or is forecast to."""
    share: Decimal
    """What this game costs one person; games can cost different amounts."""
    location: str | None = None
    start_time: time | None = None
    end_time: time | None = None
    """This night's own venue and time, or null when they are the
    season's. Display only.
    """
    venue_cost_delta: Decimal = Decimal("0")
    """What this night costs above (or below) a normal one, already
    included in `share`.
    """
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
    change_deadline_hours: int
    share_per_game: Decimal
    """What one game without air conditioning costs one person: the
    figure a season is described by. Each game carries its own
    `share`.
    """
    ac_surcharge: Decimal
    """What one game's air conditioning adds to the venue bill."""
    settled_at: datetime | None
    previous_season_settled: bool = True
    """Whether the season before this one is settled (True when there is
    none). The 繳費通知 waits for it.
    """
    members: list[MemberOut]
    games: list[GameDetailOut]


class CashAtSettlement(BaseModel):
    player_id: int
    amount: Decimal
    """Signed the way the ledger signs a payment: positive when the
    player hands money over, negative when the organizer hands it back."""


class SeasonSettleRequest(BaseModel):
    cash: list[CashAtSettlement] = Field(default_factory=list)
    """Balances squared in cash during settlement. Anyone left out keeps
    their balance for next season, which needs no entry.
    """


class FeeNoticeRecipientOut(BaseModel):
    player_id: int
    name: str
    due: Decimal
    text: str
    """The message exactly as it would be sent."""


class FeeNoticePreviewOut(BaseModel):
    recipients: list[FeeNoticeRecipientOut]


class FeeNoticeOut(BaseModel):
    sent: int
    """How many members the 繳費通知 reached."""
    unreachable: int
    """How many LINE refused (no account, or the Official Account not
    added). They stay unsent, so a later send reaches only them.
    """


class SeasonSettleOut(BaseModel):
    season_id: int
    settled_at: datetime
    members: list[MemberSettlementOut]


class PaidOutMemberOut(BaseModel):
    """Somebody whose refund was paid in cash after the season was
    settled. Undoing the settlement makes that payment read as owed
    back, so the organizer is told by name.
    """

    player_id: int
    player_name: str
    amount: Decimal
    """What was paid out, as a positive figure."""


class SeasonUnsettleOut(BaseModel):
    season_id: int
    reversed_entries: int
    """How many absence refunds were undone."""
    already_paid_out: list[PaidOutMemberOut]


class PaymentCreate(BaseModel):
    amount: Decimal
    """Signed from the player's point of view: positive means the player
    paid the organizer, negative means the organizer paid the player."""
    season_id: int | None = None
    note: str | None = None
    game_id: int | None = None
    """The night this pays for, when taken from 當天臨打. Sets the season
    too.
    """
    client_token: str | None = None
    """Generated once per intended payment. Sending the same token again
    returns the entry already recorded, so a double tap or a retry
    cannot book the money twice.
    """


class LedgerEntryOut(BaseModel):
    id: int
    entry_type: EntryType
    amount: Decimal
    recorded_at: datetime
    season_id: int | None
    note: str | None
    reverses_entry_id: int | None = None
    game_id: int | None = None


class DropInNightOut(BaseModel):
    """One night a player is on court as a drop-in, for 當天臨打."""

    game_id: int
    charged: Decimal
    """What that night's signup was charged."""
    paid: Decimal
    """Payments taken for that night, net of any undone."""


class PlayerMerge(BaseModel):
    duplicate_id: int
    """The typed-in duplicate to fold into the player in the path."""


class ProblemReport(BaseModel):
    message: str
    """What went wrong, in the reporter's own words."""
    page: str | None = None
    """Which screen they were on. Sent by the page rather than typed,
    because "it broke" is only actionable with the where."""
    user_agent: str | None = None
    club_id: int | None = None
    screenshot: str | None = None
    """A data URL. The page shrinks the picture before sending."""


class ProblemReportOut(BaseModel):
    """One stored problem report, as the developer reads it."""

    id: str
    message: str
    reporter: str
    club: str | None
    page: str | None
    user_agent: str | None
    created_at: datetime
    has_screenshot: bool
    """Fetched separately, with the developer's credentials — see
    routes/reports.problem_report_image."""
    read: bool


class DeveloperClubOut(BaseModel):
    id: int
    name: str
    organizers: list[str]
    members: int
    seasons: int
    last_activity: datetime | None
    """The latest ledger entry in the club — the closest thing to "when
    was this club last used" that every club has."""


class DeveloperOverviewOut(BaseModel):
    """Counts across the whole installation, for the developer: enough to
    notice something odd and go look. No money and no individual's
    balance.
    """

    clubs: int
    players: int
    seasons: int
    settled_seasons: int
    games: int
    upcoming_games: int
    club_members: int
    pending_members: int
    """Asked to join and still waiting — the one number that means
    somebody is stuck behind an organizer who hasn't noticed."""
    drop_ins: int
    waitlist_entries: int
    ledger_entries: int
    reports: int
    unread_reports: int
    newest_clubs: list[str]
    """Names only, newest first."""
    active_clubs: int = 0
    """Clubs with a game on within 30 days either side of today — in
    use, as against the test clubs that never got going."""
    games_this_week: int = 0
    """Games still on, Monday to Sunday of this week in Taiwan."""
    signups_this_week: int = 0
    """Drop-in signups made since Monday 00:00 in Taiwan, whatever
    became of them since."""


class DeveloperErrorOut(BaseModel):
    """A request the server failed on — see db.models.ErrorEventRow."""

    occurred_at: datetime
    method: str
    path: str
    page: str | None
    error_type: str
    message: str
    location: str


class AuditEventOut(BaseModel):
    """One entry in the audit trail — see db.models.AuditEventRow. The
    screen turns `action` into a sentence; the names are as they were
    at the time."""

    occurred_at: datetime
    action: str
    actor_name: str
    club_name: str | None
    subject_name: str | None
    amount: Decimal | None
    detail: str | None


class PlayerBalanceOut(BaseModel):
    """One player's money in one club, summed several ways at once in a
    single query for everyone on the ledger page.
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
    through_season: Decimal
    """What is due by the season asked about: its own and earlier
    seasons' entries, never a later season's. Equal to `balance` when
    no season is asked about.
    """
    nights: list[DropInNightOut] = []
    """Each night of the season asked about that they play as a drop-in,
    with what it cost and what was paid. Empty when no season is asked
    about.
    """
    brought_by: str | None = None
    """Who signed this guest up: the member who hands over the cash. None
    for anyone who signed themselves up.
    """


class PlayerLedgerOut(BaseModel):
    player_id: int
    player_name: str
    balance: Decimal
    """Positive: the organizer owes the player. Negative: the player owes."""
    season_starts: dict[int, date] = {}
    """First game date of each season these entries belong to, so the
    member screen can leave out seasons starting after the one shown.
    """
    entries: list[LedgerEntryOut]


# Both name models defined further down this file.
AbsenceCreate.model_rebuild()
AbsenceOut.model_rebuild()
