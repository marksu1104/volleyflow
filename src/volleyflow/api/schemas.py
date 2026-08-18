"""Pydantic request/response models — the API's wire format.

Kept separate from db/models.py on purpose: what a client sends and
receives isn't the same shape as a database row (a request has no id
yet; a response doesn't need every internal column).
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

from volleyflow.schedule import GameStatus


class SeasonCreate(BaseModel):
    total_venue_cost: Decimal
    game_dates: list[date] = Field(min_length=1)
    member_names: list[str] = Field(min_length=1)
    capacity: int = 18
    minimum_roster: int = 12


class GameOut(BaseModel):
    id: int
    date: date
    status: GameStatus


class SeasonOut(BaseModel):
    id: int
    total_venue_cost: Decimal
    capacity: int
    minimum_roster: int
    games: list[GameOut]
    member_ids: list[int]


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


class DropInSummary(BaseModel):
    id: int
    """The drop-in or waitlist entry id — pass this to the cancel endpoint."""
    player_name: str


class GameDetailOut(BaseModel):
    id: int
    date: date
    status: GameStatus
    absent_player_names: list[str]
    confirmed_drop_ins: list[DropInSummary]
    waitlist_entries: list[DropInSummary]


class SeasonDetailOut(BaseModel):
    id: int
    total_venue_cost: Decimal
    capacity: int
    minimum_roster: int
    members: list[MemberOut]
    games: list[GameDetailOut]
