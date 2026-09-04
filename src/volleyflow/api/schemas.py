"""Pydantic request/response models — the API's wire format.

Kept separate from db/models.py on purpose: what a client sends and
receives isn't the same shape as a database row (a request has no id
yet; a response doesn't need every internal column).
"""

from datetime import date, datetime, time
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

from volleyflow.ledger import EntryType
from volleyflow.schedule import GameStatus

Gender = Literal["male", "female"]


class SeasonCreate(BaseModel):
    total_venue_cost: Decimal
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
    capacity: int | None = None
    minimum_roster: int | None = None
    game_start_time: time | None = None
    game_end_time: time | None = None
    location: str | None = None
    change_deadline_days: int | None = None


class MemberAdd(BaseModel):
    player_name: str


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


class SubstituteCreate(BaseModel):
    player_name: str
    gender: Gender | None = None


class DropInCreate(BaseModel):
    player_name: str
    game_id: int


class DropInOut(BaseModel):
    status: Literal["confirmed", "waitlisted"]
    id: int
    player_id: int
    game_id: int


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


class GenderUpdate(BaseModel):
    gender: Gender


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
    """The drop-in player_name filling this slot, if any — FIFO by
    signup order, same rule as the refund calculation in settlement.py."""


class DropInDetailOut(BaseModel):
    id: int
    player_name: str
    gender: Gender | None = None
    covering: str | None
    """The absent member's name this drop-in is filling in for, or None
    if they're just filling an already-open slot."""


class GameDetailOut(BaseModel):
    id: int
    date: date
    status: GameStatus
    locked: bool
    """Past the season's change deadline — absences, signups, and their
    cancellations are all rejected once this is true."""
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
    """Each member or drop-in's cost for one game — the number everything
    else in billing is a multiple of. Computed here, not on the frontend:
    rounding happens in exactly one place (pricing.share_per_game)."""
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


class LedgerEntryOut(BaseModel):
    id: int
    entry_type: EntryType
    amount: Decimal
    recorded_at: datetime
    season_id: int | None
    note: str | None


class PlayerLedgerOut(BaseModel):
    player_id: int
    player_name: str
    balance: Decimal
    """Positive: the organizer owes the player. Negative: the player owes."""
    entries: list[LedgerEntryOut]
