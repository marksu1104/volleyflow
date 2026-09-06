"""SQLAlchemy table definitions.

One row class per domain concept in players.py, schedule.py, attendance.py,
and ledger.py. Membership isn't a row class of its own, same as it isn't a
Python class in the domain layer — season_members is a plain association
table with no columns beyond the two foreign keys.
"""

from datetime import date, datetime, time
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    Enum,
    ForeignKey,
    Identity,
    Index,
    Numeric,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from volleyflow.ledger import EntryType
from volleyflow.schedule import GameStatus


class Base(DeclarativeBase):
    pass


class PlayerRow(Base):
    __tablename__ = "players"

    # A name is the only identity a Player has before line_user_id exists
    # (see _get_or_create_player, which looks players up by name) — without
    # this constraint, two concurrent requests naming the same person
    # create two Players who never converge into one ledger history.
    __table_args__ = (
        UniqueConstraint("name", name="uq_players_name"),
        # NULL passes a SQL CHECK (it's neither true nor false), so this
        # still allows gender to be unset — it only rejects a bad string.
        CheckConstraint("gender IN ('male', 'female')", name="ck_players_gender"),
    )

    id: Mapped[int] = mapped_column(Identity(), primary_key=True)
    name: Mapped[str]
    gender: Mapped[str | None] = mapped_column(default=None)
    """"male" or "female", self-reported. Never used by billing — display
    only, e.g. counting how many of each are expected at a game."""


class SeasonRow(Base):
    __tablename__ = "seasons"

    # Guards against a season ever being created with numbers that would
    # make pricing.share_per_game or settlement produce a silently wrong
    # result instead of failing loudly at creation time.
    __table_args__ = (
        CheckConstraint(
            "total_venue_cost >= 0", name="ck_seasons_venue_cost_non_negative"
        ),
        CheckConstraint("capacity > 0", name="ck_seasons_capacity_positive"),
        CheckConstraint(
            "minimum_roster >= 0", name="ck_seasons_minimum_roster_non_negative"
        ),
        CheckConstraint(
            "change_deadline_days >= 0",
            name="ck_seasons_change_deadline_non_negative",
        ),
    )

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

    # season_id is already indexed as the primary key's leading column;
    # player_id isn't covered by anything until this.
    __table_args__ = (Index("ix_season_members_player_id", "player_id"),)

    season_id: Mapped[int] = mapped_column(ForeignKey("seasons.id"), primary_key=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), primary_key=True)


class GameRow(Base):
    __tablename__ = "games"

    __table_args__ = (Index("ix_games_season_id", "season_id"),)

    id: Mapped[int] = mapped_column(Identity(), primary_key=True)
    season_id: Mapped[int] = mapped_column(ForeignKey("seasons.id"))
    date: Mapped[date]
    status: Mapped[GameStatus] = mapped_column(
        Enum(GameStatus, name="game_status"), default=GameStatus.SCHEDULED
    )


class AbsenceRow(Base):
    __tablename__ = "absences"

    # A player can have only one *active* absence per game at a time —
    # two would double-count at settlement (two refunds, or the roster
    # math in _has_open_slot off by one). This is a partial index, not a
    # plain unique constraint, because cancel-then-record-again is a
    # legitimate flow (see cancel_absence): cancelled_at IS NULL excludes
    # the soft-deleted row that flow leaves behind.
    __table_args__ = (
        Index(
            "uq_absences_active_player_game",
            "player_id",
            "game_id",
            unique=True,
            postgresql_where=text("cancelled_at IS NULL"),
            sqlite_where=text("cancelled_at IS NULL"),
        ),
        # game_id isn't the leading column of anything above; player_id
        # is, so it doesn't need a separate index here.
        Index("ix_absences_game_id", "game_id"),
    )

    id: Mapped[int] = mapped_column(Identity(), primary_key=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"))
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id"))
    recorded_at: Mapped[datetime]
    cancelled_at: Mapped[datetime | None] = mapped_column(default=None)
    """Set if the member decided to attend after all. Only allowed while
    nothing is covering this absence yet — see routes.cancel_absence."""


class DropInRow(Base):
    __tablename__ = "drop_ins"

    # Same reasoning as AbsenceRow.uq_absences_active_player_game: one
    # active drop-in per player per game, but cancel-then-resign-up must
    # stay legal, hence the partial predicate rather than a plain
    # constraint.
    __table_args__ = (
        Index(
            "uq_drop_ins_active_player_game",
            "player_id",
            "game_id",
            unique=True,
            postgresql_where=text("cancelled_at IS NULL"),
            sqlite_where=text("cancelled_at IS NULL"),
        ),
        Index("ix_drop_ins_game_id", "game_id"),
        Index("ix_drop_ins_covers_absence_id", "covers_absence_id"),
    )

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

    # Hard-deleted on promotion (see _promote_from_waitlist) rather than
    # soft-deleted, so — unlike absences/drop_ins — there's never a
    # cancelled row to exclude and this can be a plain unique constraint.
    __table_args__ = (
        UniqueConstraint("player_id", "game_id", name="uq_waitlist_player_game"),
        Index("ix_waitlist_entries_game_id", "game_id"),
    )

    id: Mapped[int] = mapped_column(Identity(), primary_key=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"))
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id"))
    queued_at: Mapped[datetime]


class LedgerEntryRow(Base):
    __tablename__ = "ledger_entries"

    __table_args__ = (
        Index("ix_ledger_entries_player_id", "player_id"),
        Index("ix_ledger_entries_season_id", "season_id"),
    )

    id: Mapped[int] = mapped_column(Identity(), primary_key=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"))
    entry_type: Mapped[EntryType] = mapped_column(Enum(EntryType, name="entry_type"))
    amount: Mapped[Decimal] = mapped_column(Numeric(10, 0))
    """Signed from the player's point of view (see ledger.LedgerEntry):
    positive means the organizer owes the player, negative means the
    player owes the organizer — so this deliberately has no
    non-negative check constraint."""
    recorded_at: Mapped[datetime]
    season_id: Mapped[int | None] = mapped_column(
        ForeignKey("seasons.id"), default=None
    )
    """Which season this relates to, when there is one — a manual cash
    payment might cover more than one season, so this stays optional."""
    note: Mapped[str | None] = mapped_column(default=None)
