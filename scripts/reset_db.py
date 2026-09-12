"""Empties every table, keeping the schema, so a database can be handed
to real users with nothing in it.

Written for launch day: the production database was carrying a season's
worth of testing — half-finished clubs, a member removed and stuck, guests
typed in three different ways — and the organizer's decision was to start
clean rather than repair any of it.

It deletes rows. It does not touch the schema, and it does not touch
`alembic_version`, so the database stays exactly as migrated and the app
comes back up on an empty database rather than a broken one.

    uv run python scripts/reset_db.py            # counts what would go
    uv run python scripts/reset_db.py --yes      # backs up, then deletes

**It always takes a backup first**, to ./backups/, and refuses to delete
anything if that backup fails. That is not politeness: this is the one
script here whose whole purpose is to destroy real money records, and
`restore_db.py` is the only way back. Neon's point-in-time restore
reaches six hours on the free plan, which covers a mistake noticed the
same afternoon and nothing longer.

Against anything other than the dev branch it also asks you to type the
host name. A flag is something you can paste from history without
reading; typing the host means looking at which database you are
actually pointed at, which is exactly the check that failed the day
before this was written (see _target.py).
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

import _target
from backup_db import dump
from sqlalchemy import text

from volleyflow.db.engine import get_engine
from volleyflow.db.models import Base


def counts(conn: object) -> dict[str, int]:
    return {
        table.name: conn.execute(text(f"SELECT count(*) FROM {table.name}")).scalar()  # type: ignore[attr-defined]
        or 0
        for table in Base.metadata.sorted_tables
    }


def main() -> int:
    is_dev = _target.announce("about to empty")
    print()

    engine = get_engine()
    with engine.connect() as conn:
        before = counts(conn)
    total = sum(before.values())
    for name, n in before.items():
        print(f"  {name:<18} {n:>6}")
    print(f"  {'':<18} {'-' * 6}\n  {'total':<18} {total:>6}")

    if total == 0:
        print("\nAlready empty. Nothing to do.")
        return 0

    if "--yes" not in sys.argv:
        print("\nNothing deleted. Re-run with --yes to empty it.")
        return 0

    if not is_dev:
        host, _ = _target.describe()
        if not sys.stdin.isatty():
            print(
                "\nRefusing: emptying a database that is not the dev branch "
                "needs a terminal, so the host can be typed back.",
                file=sys.stderr,
            )
            return 1
        typed = input(f"\nType the host to confirm ({host}): ").strip()
        if typed != host:
            print("That is not the host. Nothing deleted.", file=sys.stderr)
            return 1

    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    backup = Path("backups") / f"before-reset-{stamp}.json"
    backup.parent.mkdir(exist_ok=True)
    print(f"\nbacking up to {backup} ...")
    try:
        dump(backup)
    except Exception as e:  # noqa: BLE001 — the message matters, not the type
        print(f"Backup failed, so nothing was deleted: {e}", file=sys.stderr)
        return 1
    print(f"  {backup.stat().st_size:,} bytes")

    # Children before parents, which is what sorted_tables gives in
    # reverse — the same order restore_db.py relies on, and the reason
    # this doesn't need the schema to carry cascades.
    with engine.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            conn.execute(text(f"DELETE FROM {table.name}"))

    with engine.connect() as conn:
        after = sum(counts(conn).values())
    print(f"\ndeleted {total} rows; {after} remain")
    print(f"restore with: uv run python scripts/restore_db.py {backup} --yes")
    return 0 if after == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
