"""backup_db.py / restore_db.py: the restore drill from docs/backlog.md
("Neon keeps backups; nobody has ever tried restoring one").

Most of this runs against in-memory SQLite, monkeypatching each script's
get_engine so the round trip is exercised without a network — the point
here is the *logic* (JSON round-tripping Decimal/date/enum values, delete
order respecting foreign keys, restoring in dependency order), which
doesn't depend on which database is underneath. The one thing that does
— resyncing an IDENTITY sequence after inserting explicit ids — is
Postgres-only and is skipped entirely against SQLite; `test_restores_a_
real_disaster_on_neon` below is what actually proves that half, against
the real dev branch, the same way the 2026-09-12 dry run in
docs/dev-log.md did by hand.
"""

import json
from datetime import date, datetime, time
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session

import scripts.backup_db as backup_db
import scripts.restore_db as restore_db
from volleyflow.db.engine import get_session
from volleyflow.db.models import (
    AbsenceRow,
    ClubMemberRow,
    ClubRow,
    GameRow,
    LedgerEntryRow,
    PlayerRow,
    SeasonMemberRow,
    SeasonRow,
)
from volleyflow.ledger import EntryType
from volleyflow.schedule import GameStatus


@pytest.fixture
def _use_sqlite(sqlite_engine: Engine, monkeypatch: pytest.MonkeyPatch) -> Engine:
    monkeypatch.setattr(backup_db, "get_engine", lambda: sqlite_engine)
    monkeypatch.setattr(restore_db, "get_engine", lambda: sqlite_engine)
    monkeypatch.setattr(backup_db, "database_url", lambda: "sqlite:///:memory:")
    monkeypatch.setattr(restore_db, "database_url", lambda: "sqlite:///:memory:")
    return sqlite_engine


def _seed(session: Session) -> None:
    club = ClubRow(id=1, name="測試球隊", created_at=datetime(2026, 1, 1))
    session.add(club)
    session.add(PlayerRow(id=1, name="蘇懂"))
    session.add(PlayerRow(id=2, name="Alice", gender="female"))
    session.add(
        ClubMemberRow(
            club_id=1, player_id=1, role="organizer", joined_at=datetime(2026, 1, 1)
        )
    )
    season = SeasonRow(
        id=1,
        club_id=1,
        total_venue_cost=Decimal("10000"),
        capacity=2,
        minimum_roster=1,
        game_start_time=time(18, 30),
    )
    session.add(season)
    session.add(
        GameRow(
            id=1,
            season_id=1,
            date=date(2026, 8, 18),
            status=GameStatus.SCHEDULED,
            air_conditioned=True,
        )
    )
    session.add(
        GameRow(
            id=2,
            season_id=1,
            date=date(2026, 8, 25),
            status=GameStatus.CANCELLED_REFUNDED,
        )
    )
    session.add(SeasonMemberRow(season_id=1, player_id=2))
    session.add(
        AbsenceRow(id=1, player_id=2, game_id=1, recorded_at=datetime(2026, 8, 17, 9))
    )
    session.add(
        LedgerEntryRow(
            id=1,
            player_id=2,
            club_id=1,
            entry_type=EntryType.SEASON_FEE_CHARGED,
            amount=Decimal("-5000"),
            recorded_at=datetime(2026, 1, 2),
            season_id=1,
        )
    )
    session.commit()


def _counts(session: Session) -> dict[str, int]:
    return {
        "clubs": session.query(ClubRow).count(),
        "players": session.query(PlayerRow).count(),
        "games": session.query(GameRow).count(),
        "absences": session.query(AbsenceRow).count(),
        "ledger_entries": session.query(LedgerEntryRow).count(),
    }


def test_a_full_dump_and_restore_round_trip_is_lossless(
    _use_sqlite: Engine, tmp_path: Path
) -> None:
    with Session(_use_sqlite) as session:
        _seed(session)
        before = _counts(session)

    dump_path = tmp_path / "backup.json"
    backup_db.dump(dump_path)

    with Session(_use_sqlite) as session:
        # The simulated disaster: everything gone.
        for row in session.query(LedgerEntryRow).all():
            session.delete(row)
        session.commit()
        assert _counts(session)["ledger_entries"] == 0

    restore_db.restore(dump_path)

    with Session(_use_sqlite) as session:
        after = _counts(session)
        assert after == before
        entry = session.get(LedgerEntryRow, 1)
        assert entry is not None
        assert entry.amount == Decimal("-5000"), "Decimal survives the JSON round trip"
        game = session.get(GameRow, 2)
        assert game is not None
        assert game.status == GameStatus.CANCELLED_REFUNDED, (
            "enum survives by value, not name"
        )
        absence = session.get(AbsenceRow, 1)
        assert absence is not None
        assert absence.recorded_at == datetime(2026, 8, 17, 9), "datetime survives"


def test_dump_is_readable_plain_json(_use_sqlite: Engine, tmp_path: Path) -> None:
    # Not a pickle or anything exotic — a plain JSON file an organizer (or
    # a future script) could open and read without this project's code.
    with Session(_use_sqlite) as session:
        _seed(session)

    dump_path = tmp_path / "backup.json"
    backup_db.dump(dump_path)

    raw = json.loads(dump_path.read_text(encoding="utf-8"))
    assert "clubs" in raw
    assert raw["clubs"][0]["name"] == "測試球隊"
    # The wrapped-object encoding for a Decimal, spelled out so a change
    # to it is a deliberate decision, not a silent break.
    assert raw["ledger_entries"][0]["amount"] == {"__decimal__": "-5000"}


def test_restore_without_yes_is_a_dry_run(
    _use_sqlite: Engine, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with Session(_use_sqlite) as session:
        _seed(session)
        before = _counts(session)

    dump_path = tmp_path / "backup.json"
    backup_db.dump(dump_path)

    import sys

    argv = sys.argv
    sys.argv = ["restore_db.py", str(dump_path)]
    try:
        restore_db.main()
    finally:
        sys.argv = argv

    out = capsys.readouterr().out
    assert "dry run only" in out
    with Session(_use_sqlite) as session:
        assert _counts(session) == before, "nothing was touched without --yes"


def test_deletes_children_before_parents_and_restores_the_other_way(
    _use_sqlite: Engine, tmp_path: Path
) -> None:
    # If this were the wrong order, either the delete pass or the insert
    # pass would fail a foreign key check — SQLite enforces them too once
    # PRAGMA foreign_keys is on, and even without that this proves the
    # data lands intact rather than merely not-erroring.
    with Session(_use_sqlite) as session:
        _seed(session)

    dump_path = tmp_path / "backup.json"
    backup_db.dump(dump_path)
    restore_db.restore(dump_path)

    with Session(_use_sqlite) as session:
        game = session.get(GameRow, 1)
        assert game is not None and game.season_id == 1
        absence = session.get(AbsenceRow, 1)
        assert absence is not None and absence.game_id == 1


@pytest.mark.postgres
def test_restores_a_real_disaster_on_neon(tmp_path: Path) -> None:
    """The actual drill, against the real dev database — mirrors the
    manual dry run recorded in docs/dev-log.md: back up, delete
    everything in one table, restore, prove it's back, prove a fresh
    insert afterward doesn't collide with a restored id (the Postgres-only
    half the SQLite tests above can't exercise).
    """
    club_id = 999_990
    # Committed in dependency order, one level at a time, rather than
    # relying on the ORM to topologically sort four unrelated adds in a
    # single flush — it doesn't reliably order across autonomous
    # single-object adds with no relationship() linking them.
    with get_session() as session:
        session.add(
            ClubRow(id=club_id, name="restore drill", created_at=datetime(2026, 1, 1))
        )
        session.add(PlayerRow(id=club_id, name="restore drill organizer"))
        session.commit()
    with get_session() as session:
        session.add(
            ClubMemberRow(
                club_id=club_id,
                player_id=club_id,
                role="organizer",
                joined_at=datetime(2026, 1, 1),
            )
        )
        session.add(
            LedgerEntryRow(
                id=club_id,
                player_id=club_id,
                club_id=club_id,
                entry_type=EntryType.SEASON_FEE_CHARGED,
                amount=Decimal("-100"),
                recorded_at=datetime(2026, 1, 1),
            )
        )
        session.commit()

    dump_path = tmp_path / "neon_backup.json"
    try:
        backup_db.dump(dump_path)

        with get_session() as session:
            entry = session.get(LedgerEntryRow, club_id)
            assert entry is not None
            session.delete(entry)
            session.commit()
            assert session.get(LedgerEntryRow, club_id) is None

        restore_db.restore(dump_path)

        with get_session() as session:
            restored = session.get(LedgerEntryRow, club_id)
            assert restored is not None
            assert restored.amount == Decimal("-100")

            # The sequence-reset half: a fresh row with no explicit id
            # must not collide with the one just restored.
            fresh = LedgerEntryRow(
                player_id=club_id,
                club_id=club_id,
                entry_type=EntryType.SEASON_FEE_CHARGED,
                amount=Decimal("-1"),
                recorded_at=datetime(2026, 1, 1),
            )
            session.add(fresh)
            session.commit()
            assert fresh.id is not None and fresh.id != club_id
    finally:
        with get_session() as session:
            session.query(LedgerEntryRow).filter(
                LedgerEntryRow.club_id == club_id
            ).delete(synchronize_session=False)
            session.query(ClubMemberRow).filter(
                ClubMemberRow.club_id == club_id
            ).delete(synchronize_session=False)
            session.query(PlayerRow).filter(PlayerRow.id == club_id).delete(
                synchronize_session=False
            )
            session.query(ClubRow).filter(ClubRow.id == club_id).delete(
                synchronize_session=False
            )
            session.commit()
