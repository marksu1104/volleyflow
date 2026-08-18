"""SQLAlchemy table definitions.

One row class per domain concept in players.py, schedule.py, attendance.py,
and ledger.py. Membership isn't a row class of its own, same as it isn't a
Python class in the domain layer — season_members is a plain association
table with no columns beyond the two foreign keys.
"""

from datetime import date, datetime
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


class SeasonRow(Base):
    __tablename__ = "seasons"

    id: Mapped[int] = mapped_column(Identity(), primary_key=True)
    total_venue_cost: Mapped[Decimal] = mapped_column(Numeric(10, 0))
    capacity: Mapped[int] = mapped_column(default=18)
    minimum_roster: Mapped[int] = mapped_column(default=12)
    """Below this expected attendance, the organizer gets a short-roster
    alert. Default 12 is two full 6-a-side sides."""


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


class DropInRow(Base):
    __tablename__ = "drop_ins"

    id: Mapped[int] = mapped_column(Identity(), primary_key=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"))
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id"))
    signed_up_at: Mapped[datetime]
    cancelled_at: Mapped[datetime | None] = mapped_column(default=None)


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
