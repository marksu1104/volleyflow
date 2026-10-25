"""SQLAlchemy table definitions.

One row class per domain concept in players.py, schedule.py,
attendance.py and ledger.py. A season's fixed membership is the
season_members association table, with no columns beyond its two keys.
"""

from datetime import date, datetime, time
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    ColumnElement,
    Enum,
    ForeignKey,
    Identity,
    Index,
    Numeric,
    UniqueConstraint,
    and_,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from volleyflow.ledger import EntryType
from volleyflow.schedule import GameStatus


class Base(DeclarativeBase):
    pass


class ClubRow(Base):
    """The tenant boundary: one volleyball group with its own organizer,
    members, seasons and books.
    """

    __tablename__ = "clubs"

    # No unique name: two clubs may share one.
    id: Mapped[int] = mapped_column(Identity(), primary_key=True)
    name: Mapped[str]
    created_at: Mapped[datetime]


class ClubMemberRow(Base):
    """A Player's relationship to a Club and their role in it. Club-wide,
    unlike SeasonMemberRow: somebody can belong to a club without
    being on any season's roster.
    """

    __tablename__ = "club_members"

    __table_args__ = (
        CheckConstraint("role IN ('organizer', 'member')", name="ck_club_members_role"),
        CheckConstraint(
            "status IN ('pending', 'active')", name="ck_club_members_status"
        ),
        Index("ix_club_members_player_id", "player_id"),
    )

    club_id: Mapped[int] = mapped_column(ForeignKey("clubs.id"), primary_key=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), primary_key=True)
    role: Mapped[str]
    joined_at: Mapped[datetime]
    status: Mapped[str] = mapped_column(default="active", server_default="active")
    """'pending' after joining by link until the organizer approves, then
    'active'. A pending member can see and do nothing in the club.
    """
    wants_fixed_membership: Mapped[bool | None] = mapped_column(default=None)
    """What they asked to be when they joined. NULL: never asked. True: a
    fixed member, waiting for the organizer to put them on a roster.
    False: here for single games, able to sign up straight away. Only
    a stated intention; billing never reads it.
    """


class PlayerRow(Base):
    __tablename__ = "players"

    # line_user_id is the identity once set; names are unique only within a
    # club, enforced by _get_or_create_player.
    __table_args__ = (
        # Allows any number of NULLs, for people typed in by name.
        UniqueConstraint("line_user_id", name="uq_players_line_user_id"),
        # NULL passes a CHECK, so gender may be unset.
        CheckConstraint("gender IN ('male', 'female')", name="ck_players_gender"),
    )

    id: Mapped[int] = mapped_column(Identity(), primary_key=True)
    name: Mapped[str]
    gender: Mapped[str | None] = mapped_column(default=None)
    """"male" or "female", self-reported. Display only, never used by
    billing.
    """
    line_user_id: Mapped[str | None] = mapped_column(default=None)
    """The LINE identity, set the first time they open the app. None for
    somebody typed in by name; linking them to a LINE account is the
    organizer's decision.
    """
    avatar_url: Mapped[str | None] = mapped_column(default=None)
    """LINE profile picture, refreshed on every visit. Display only."""


class SeasonRow(Base):
    __tablename__ = "seasons"

    # Numbers that would make pricing fail silently are refused at creation.
    __table_args__ = (
        CheckConstraint(
            "total_venue_cost >= 0", name="ck_seasons_venue_cost_non_negative"
        ),
        CheckConstraint("capacity > 0", name="ck_seasons_capacity_positive"),
        CheckConstraint(
            "minimum_roster >= 0", name="ck_seasons_minimum_roster_non_negative"
        ),
        CheckConstraint(
            "change_deadline_hours >= 0",
            name="ck_seasons_change_deadline_non_negative",
        ),
        CheckConstraint(
            "ac_surcharge >= 0", name="ck_seasons_ac_surcharge_non_negative"
        ),
        Index("ix_seasons_club_id", "club_id"),
    )

    id: Mapped[int] = mapped_column(Identity(), primary_key=True)
    club_id: Mapped[int] = mapped_column(ForeignKey("clubs.id"))
    """The season's club. Games, absences, drop-ins and season membership
    reach their club through this, never storing their own club_id.
    """
    total_venue_cost: Mapped[Decimal] = mapped_column(Numeric(10, 0))
    """What the club actually pays the venue for the season.
    Authoritative: the air-conditioning portion is taken out of it,
    not added on top.
    """
    ac_surcharge: Mapped[Decimal] = mapped_column(Numeric(10, 0), default=Decimal("0"))
    """What one game's air conditioning adds to the venue bill; 0 when
    included. See pricing.shares_by_game.
    """
    capacity: Mapped[int] = mapped_column(default=18)
    minimum_roster: Mapped[int] = mapped_column(default=12)
    """The fewest players a game should have. Default 12 is two sides of
    six.
    """
    settled_at: Mapped[datetime | None] = mapped_column(default=None)
    """When the season was settled; a settled season's books and
    attendance are closed.
    """
    game_start_time: Mapped[time | None] = mapped_column(default=None)
    game_end_time: Mapped[time | None] = mapped_column(default=None)
    """The usual weekly time slot. Display only."""
    location: Mapped[str | None] = mapped_column(default=None)
    """The venue name. Display only."""
    change_deadline_hours: Mapped[int] = mapped_column(default=24, server_default="24")
    """How many hours before a game members' attendance changes close; 24
    means "until this time the day before". Also when the roster
    status and promotion notices go out, so every season has one.
    """


class SeasonMemberRow(Base):
    """A Player's fixed membership of a Season."""

    __tablename__ = "season_members"

    # season_id leads the primary key; player_id needs its own index.
    __table_args__ = (Index("ix_season_members_player_id", "player_id"),)

    season_id: Mapped[int] = mapped_column(ForeignKey("seasons.id"), primary_key=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), primary_key=True)
    fee_notice_sent_at: Mapped[datetime | None] = mapped_column(default=None)
    """When this member was sent the season's 繳費通知. A LINE message cannot
    be taken back, so it is sent at most once a season.
    """


class GameRow(Base):
    __tablename__ = "games"

    __table_args__ = (Index("ix_games_season_id", "season_id"),)

    id: Mapped[int] = mapped_column(Identity(), primary_key=True)
    season_id: Mapped[int] = mapped_column(ForeignKey("seasons.id"))
    date: Mapped[date]
    status: Mapped[GameStatus] = mapped_column(
        Enum(GameStatus, name="game_status"), default=GameStatus.SCHEDULED
    )
    air_conditioned: Mapped[bool] = mapped_column(default=False)
    """Whether the air conditioning ran: a forecast at booking, corrected
    on the night. Changing it moves the season's total by ac_surcharge
    and re-syncs every member's charge; see
    routes.set_game_air_conditioning.
    """
    location: Mapped[str | None] = mapped_column(default=None)
    roster_notice_sent_at: Mapped[datetime | None] = mapped_column(default=None)
    """When the game's deadline notices went out. Set before sending, so
    the job that runs every ten minutes sends them once.
    """
    start_time: Mapped[time | None] = mapped_column(default=None)
    end_time: Mapped[time | None] = mapped_column(default=None)
    """This night's own venue and time, when different from the season's.
    NULL means the season's. Display only: billing never reads them.
    """
    venue_cost_delta: Mapped[Decimal] = mapped_column(
        Numeric(10, 0), default=Decimal("0")
    )
    """What this night costs above (or below) a normal one, when it runs
    somewhere else. Priced like ac_surcharge, so it lands only on that
    night's players. Changing it moves `total_venue_cost` and re-syncs
    every member's charge. See docs/billing-rules.md "A different
    venue for one night".
    """


class AbsenceRow(Base):
    __tablename__ = "absences"

    # One live absence per player per game. Partial, so cancelling and
    # recording again stays possible.
    __table_args__ = (
        Index(
            "uq_absences_active_player_game",
            "player_id",
            "game_id",
            unique=True,
            postgresql_where=text("cancelled_at IS NULL"),
            sqlite_where=text("cancelled_at IS NULL"),
        ),
        # game_id leads no index above; player_id does.
        Index("ix_absences_game_id", "game_id"),
    )

    id: Mapped[int] = mapped_column(Identity(), primary_key=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"))
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id"))
    recorded_at: Mapped[datetime]
    cancelled_at: Mapped[datetime | None] = mapped_column(default=None)
    """Set when the member decides to play after all; see
    routes.cancel_absence.
    """
    retired_at: Mapped[datetime | None] = mapped_column(default=None)
    """Set with cancelled_at when the absence was closed because the
    player was taken off the roster, so putting them back can restore
    it. See routes._restore_retired_absences and
    DropInRow.absorbed_at.
    """


PLAYING = "playing"
QUEUED = "queued"


class DropInRow(Base):
    """One person's signup for one game, on court or waiting in the
    queue. Moving between the two changes `status` and nothing else.
    """

    __tablename__ = "drop_ins"

    # One live signup per player per game, partial for the same reason as
    # absences.
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
        CheckConstraint("status IN ('playing', 'queued')", name="ck_drop_ins_status"),
    )

    id: Mapped[int] = mapped_column(Identity(), primary_key=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"))
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id"))
    signed_up_at: Mapped[datetime]
    """When they took the place on court they hold, which FIFO coverage
    and "the latest signup yields" order by. For somebody promoted off
    the queue, when they joined it.
    """
    cancelled_at: Mapped[datetime | None] = mapped_column(default=None)
    status: Mapped[str] = mapped_column(default=PLAYING, server_default=PLAYING)
    """`playing` (on court, charged) or `queued` (waiting, not charged).
    Live only while `cancelled_at` is null; queries use `playing()`
    and `queued()`.
    """
    queued_at: Mapped[datetime | None] = mapped_column(default=None)
    """When they joined the queue, if they ever did: the queue's order,
    and the place they are owed back if a slot is taken off them. Null
    for somebody never in the queue, whose signup is cancelled
    instead.
    """

    @classmethod
    def playing(cls) -> ColumnElement[bool]:
        """On court: live and not waiting."""
        return and_(cls.cancelled_at.is_(None), cls.status == PLAYING)

    @classmethod
    def queued(cls) -> ColumnElement[bool]:
        """In the queue: live and waiting."""
        return and_(cls.cancelled_at.is_(None), cls.status == QUEUED)

    absorbed_at: Mapped[datetime | None] = mapped_column(default=None)
    """Set when the system cancelled this signup because the player
    became a fixed member, so taking them off the roster restores
    exactly these and never a signup they cancelled themselves.
    """
    covers_absence_id: Mapped[int | None] = mapped_column(
        ForeignKey("absences.id"), default=None
    )
    """The absence this drop-in was personally named to cover (a 代打),
    rather than a signup matched by FIFO order.
    """
    brought_by_player_id: Mapped[int | None] = mapped_column(
        ForeignKey("players.id"), default=None
    )
    """Who put this person on the list, when it was not themselves: whom
    the organizer asks to make sure they pay. The fee is on the
    person's own ledger. Set once and never changed, including when
    they move between court and queue or are named a 代打.
    """
    charged_amount: Mapped[Decimal | None] = mapped_column(Numeric(10, 0), default=None)
    """What this signup was charged, so cancelling refunds exactly that
    even if the game's share has changed since. Null on older rows,
    which fall back to the current share.
    """

    promotion_notice_sent_at: Mapped[datetime | None] = mapped_column(default=None)
    """When this signup's 遞補通知 went out. Set by the deadline job, which
    alone sends promotion notices, so each person is told once. Never
    cleared.
    """


class ProblemReportRow(Base):
    """A problem report somebody sent, kept for the developer to read.

    Who sent it and from which club and screen are text, not foreign
    keys: a snapshot that must not block deleting the club or change
    when it is renamed. Rows go after 90 days.
    """

    __tablename__ = "problem_reports"

    id: Mapped[str] = mapped_column(primary_key=True)
    """Random, so holding one report's id says nothing about any other."""
    message: Mapped[str | None] = mapped_column(default=None)
    """NULL only on old rows that held a screenshot and no text; never
    listed.
    """
    reporter: Mapped[str | None] = mapped_column(default=None)
    club: Mapped[str | None] = mapped_column(default=None)
    page: Mapped[str | None] = mapped_column(default=None)
    user_agent: Mapped[str | None] = mapped_column(default=None)
    image: Mapped[bytes | None] = mapped_column(default=None)
    content_type: Mapped[str | None] = mapped_column(default=None)
    created_at: Mapped[datetime]
    read_at: Mapped[datetime | None] = mapped_column(default=None)


class LedgerEntryRow(Base):
    __tablename__ = "ledger_entries"

    __table_args__ = (
        Index("ix_ledger_entries_player_id", "player_id"),
        Index("ix_ledger_entries_season_id", "season_id"),
        Index("ix_ledger_entries_club_id", "club_id"),
        Index("ix_ledger_entries_game_id", "game_id"),
        # One undo per payment: a repeated 復原 must not reverse it twice.
        Index(
            "uq_ledger_entries_reverses_entry_id",
            "reverses_entry_id",
            unique=True,
            postgresql_where=text("reverses_entry_id IS NOT NULL"),
            sqlite_where=text("reverses_entry_id IS NOT NULL"),
        ),
        # Makes recording a payment safe to repeat: the same client token
        # cannot be stored twice. Partial, because only client-initiated
        # entries carry a token.
        Index(
            "uq_ledger_entries_client_token",
            "client_token",
            unique=True,
            postgresql_where=text("client_token IS NOT NULL"),
            sqlite_where=text("client_token IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(Identity(), primary_key=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"))
    club_id: Mapped[int] = mapped_column(ForeignKey("clubs.id"))
    """Stored directly, unlike every other table: season_id is optional,
    so there is not always a chain of foreign keys to a club.
    """
    entry_type: Mapped[EntryType] = mapped_column(Enum(EntryType, name="entry_type"))
    amount: Mapped[Decimal] = mapped_column(Numeric(10, 0))
    """Signed from the player's point of view: positive means the
    organizer owes the player.
    """
    recorded_at: Mapped[datetime]
    season_id: Mapped[int | None] = mapped_column(
        ForeignKey("seasons.id"), default=None
    )
    """The season this relates to, when there is one."""
    note: Mapped[str | None] = mapped_column(default=None)
    reverses_entry_id: Mapped[int | None] = mapped_column(
        ForeignKey("ledger_entries.id"), default=None
    )
    """Set on the entry that undoes a mistaken payment; the original stays."""
    client_token: Mapped[str | None] = mapped_column(default=None)
    """Generated by the client once per intended action, so a retried or
    double-tapped request lands once. See the partial unique index
    above.
    """
    game_id: Mapped[int | None] = mapped_column(ForeignKey("games.id"), default=None)
    """The night a drop-in payment was taken for, from 當天臨打; null for
    other entries. An undo carries the same night as the payment it
    reverses.
    """


class ErrorEventRow(Base):
    """A request the server failed on, kept for the developer's overview.
    Text rather than foreign keys, like a problem report. Rows go
    after 30 days.
    """

    __tablename__ = "error_events"
    __table_args__ = (Index("ix_error_events_occurred_at", "occurred_at"),)

    id: Mapped[int] = mapped_column(Identity(), primary_key=True)
    occurred_at: Mapped[datetime]
    method: Mapped[str]
    path: Mapped[str]
    page: Mapped[str | None] = mapped_column(default=None)
    """The screen the request came from (its Referer's file name)."""
    error_type: Mapped[str]
    message: Mapped[str]
    location: Mapped[str]
    """The deepest line of this project's own code the error passed
    through.
    """


class AuditEventRow(Base):
    """Who did what to whose money or membership, and when.

    Written in the same transaction as the change, so the two always
    agree. Names are copied in, so the record outlives merges, renames
    and deleted clubs. Never updated or deleted.
    """

    __tablename__ = "audit_events"
    __table_args__ = (Index("ix_audit_events_occurred_at", "occurred_at"),)

    id: Mapped[int] = mapped_column(Identity(), primary_key=True)
    occurred_at: Mapped[datetime]
    action: Mapped[str]
    actor_id: Mapped[int | None] = mapped_column(default=None)
    actor_name: Mapped[str]
    club_id: Mapped[int | None] = mapped_column(default=None)
    club_name: Mapped[str | None] = mapped_column(default=None)
    subject_name: Mapped[str | None] = mapped_column(default=None)
    amount: Mapped[Decimal | None] = mapped_column(Numeric(10, 0), default=None)
    detail: Mapped[str | None] = mapped_column(default=None)
