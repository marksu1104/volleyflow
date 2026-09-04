"""SQLAlchemy table definitions.

One row class per domain concept in players.py, schedule.py, attendance.py,
and ledger.py. Membership isn't a row class of its own, same as it isn't a
Python class in the domain layer — season_members is a plain association
table with no columns beyond the two foreign keys.
"""

from datetime import date, datetime, time
from decimal import Decimal

from sqlalchemy import Enum, ForeignKey, Identity, Numeric
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from volleyflow.ledger import EntryType
from volleyflow.schedule import GameStatus


class Base(DeclarativeBase):
    pass


class PlayerRow(Base):
    __tablename__ = "players"

    id: Mapped[int] = mapped_column(Identity(), primary_key=True)
    name: Mapped[str]
    gender: Mapped[str | None] = mapped_column(default=None)
    """"male" or "female", self-reported. Never used by billing — display
    only, e.g. counting how many of each are expected at a game."""


class SeasonRow(Base):
    __tablename__ = "seasons"

    id: Mapped[int] = mapped_column(Identity(), primary_key=True)
    total_venue_cost: Mapped[Decimal] = mapped_column(Numeric(10, 0))
    capacity: Mapped[int] = mapped_column(default=18)
    minimum_roster: Mapped[int] = mapped_column(default=12)
    """Below this expected attendance, the organizer gets a short-roster
    alert. Default 12 is two full 6-a-side sides."""
    settled_at: Mapped[datetime | None] = mapped_column(default=None)
    """Set once /settle has run for this season — guards against
    charging member season fees twice."""
    game_start_time: Mapped[time | None] = mapped_column(default=None)
    game_end_time: Mapped[time | None] = mapped_column(default=None)
    """The season's fixed weekly time slot, e.g. 18:30-22:00. Optional —
    billing and attendance never depend on it, it's only shown to people
    and included in reminder messages."""
    location: Mapped[str | None] = mapped_column(default=None)
    """The venue name, e.g. "啪排郎". Same reasoning as the time slot:
    display-only, optional."""
    change_deadline_days: Mapped[int | None] = mapped_column(default=None)
    """How many days before a game attendance changes (absence, signup,
    cancelling either) are still allowed — 1 means "up to the day
    before." None means no deadline, CLAUDE.md 2.3's stated default."""


class SeasonMemberRow(Base):
    """Membership: a Player's fixed-member relationship to a Season."""

    __tablename__ = "season_members"

    season_id: Mapped[int] = mapped_column(ForeignKey("seasons.id"), primary_key=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), primary_key=True)


class GameRow(Base):
    __tablename__ = "games"

    id: Mapped[int] = mapped_column(Identity(), primary_key=True)
    season_id: Mapped[int] = mapped_column(ForeignKey("seasons.id"))
    date: Mapped[date]
    status: Mapped[GameStatus] = mapped_column(
        Enum(GameStatus, name="game_status"), default=GameStatus.SCHEDULED
    )


class AbsenceRow(Base):
    __tablename__ = "absences"

    id: Mapped[int] = mapped_column(Identity(), primary_key=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"))
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id"))
    recorded_at: Mapped[datetime]
    cancelled_at: Mapped[datetime | None] = mapped_column(default=None)
    """Set if the member decided to attend after all. Only allowed while
    nothing is covering this absence yet — see routes.cancel_absence."""


class DropInRow(Base):
    __tablename__ = "drop_ins"

    id: Mapped[int] = mapped_column(Identity(), primary_key=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"))
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id"))
    signed_up_at: Mapped[datetime]
    cancelled_at: Mapped[datetime | None] = mapped_column(default=None)
    covers_absence_id: Mapped[int | None] = mapped_column(
        ForeignKey("absences.id"), default=None
    )
    """Set when this drop-in is a member's own named substitute ("代打")
    for that specific absence, rather than a general signup matched by
    FIFO order — see attendance.DropIn.covers."""


class WaitlistEntryRow(Base):
    __tablename__ = "waitlist_entries"

    id: Mapped[int] = mapped_column(Identity(), primary_key=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"))
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id"))
    queued_at: Mapped[datetime]


class LedgerEntryRow(Base):
    __tablename__ = "ledger_entries"

    id: Mapped[int] = mapped_column(Identity(), primary_key=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"))
    entry_type: Mapped[EntryType] = mapped_column(Enum(EntryType, name="entry_type"))
    amount: Mapped[Decimal] = mapped_column(Numeric(10, 0))
    recorded_at: Mapped[datetime]
    season_id: Mapped[int | None] = mapped_column(
        ForeignKey("seasons.id"), default=None
    )
    """Which season this relates to, when there is one — a manual cash
    payment might cover more than one season, so this stays optional."""
    note: Mapped[str | None] = mapped_column(default=None)
