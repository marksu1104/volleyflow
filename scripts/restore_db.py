"""Restores a database from a backup_db.py dump. Destructive.

Every row currently in the database is deleted and replaced with what
the dump file holds — this is a *restore*, not a merge. Refuses to run
without --yes, and prints which host it's about to do this to before it
does anything, since DATABASE_URL is the only thing standing between
"restore the dev branch" and "restore production."

    uv run python scripts/restore_db.py backups/volleyflow-20260912-090000.json --yes

See backup_db.py for the dump format this reads, and docs/dev-log.md for
the 2026-09-12 dry run this was written for and tested against.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, time
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import text

from volleyflow.db.engine import database_url, get_engine
from volleyflow.db.models import Base


def _json_object_hook(obj: dict[str, Any]) -> Any:
    if "__decimal__" in obj:
        return Decimal(obj["__decimal__"])
    if "__datetime__" in obj:
        return datetime.fromisoformat(obj["__datetime__"])
    if "__date__" in obj:
        return date.fromisoformat(obj["__date__"])
    if "__time__" in obj:
        return time.fromisoformat(obj["__time__"])
    return obj


def _restore_enums(table: Any, rows: list[dict[str, Any]]) -> None:
    """Puts a game's status and a ledger entry's type back into the form
    SQLAlchemy's Enum type expects to bind: an actual member of the
    Python enum, not the plain string backup_db.py had to serialize it
    as.

    Found by actually running this restore against the dev branch: the
    column stores each label as the enum member's *name* ("SCHEDULED"),
    but the dump holds its *value* ("scheduled") — the form the rest of
    the app uses — so inserting the raw string back verbatim reached
    Postgres as a label the enum type doesn't have.
    """
    for column in table.columns:
        enum_class = getattr(column.type, "enum_class", None)
        if enum_class is None:
            continue
        for row in rows:
            value = row.get(column.name)
            if value is not None and not isinstance(value, enum_class):
                row[column.name] = enum_class(value)


def restore(dump_path: Path) -> None:
    data: dict[str, list[dict[str, Any]]] = json.loads(
        dump_path.read_text(encoding="utf-8"), object_hook=_json_object_hook
    )

    engine = get_engine()
    with engine.begin() as conn:
        # Children before parents, so a foreign key never points at a row
        # that's already gone.
        for table in reversed(Base.metadata.sorted_tables):
            conn.execute(table.delete())

        # Parents before children, the reverse of above, so a foreign key
        # never points at a row that doesn't exist yet.
        for table in Base.metadata.sorted_tables:
            rows = data.get(table.name, [])
            _restore_enums(table, rows)
            if rows:
                conn.execute(table.insert(), rows)
            print(f"  {table.name}: restored {len(rows)} rows")

            # An IDENTITY column's own counter doesn't know rows were just
            # inserted with explicit ids — left alone, the next ordinary
            # insert (no id given) would collide with the highest id just
            # restored. Not every table's "id" is one, though —
            # problem_reports.id is an unguessable text token, not a
            # sequence — so this asks Postgres what the sequence actually
            # is rather than assuming every "id" column has one; a null
            # answer means there's nothing to resync. Postgres-only: the
            # concept doesn't exist on SQLite, which is what the test
            # suite's non-postgres tests restore against.
            if engine.dialect.name == "postgresql" and "id" in table.columns:
                sequence = conn.execute(
                    text("SELECT pg_get_serial_sequence(:t, 'id')"), {"t": table.name}
                ).scalar()
                if sequence:
                    conn.execute(
                        text(
                            "SELECT setval(:seq, "
                            "COALESCE((SELECT MAX(id) FROM " + table.name + "), 1), "
                            "(SELECT MAX(id) FROM " + table.name + ") IS NOT NULL)"
                        ),
                        {"seq": sequence},
                    )

    total = sum(len(rows) for rows in data.values())
    print(f"restored {total} rows across {len(data)} tables")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dump", type=Path, help="A file written by backup_db.py")
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Actually do it. Without this, only says what it would do.",
    )
    args = parser.parse_args()

    host = urlsplit(database_url()).hostname
    print(f"target database: {host}")
    print(f"dump file: {args.dump}")
    if not args.yes:
        print("dry run only — pass --yes to actually delete and restore")
        return 0

    restore(args.dump)
    return 0


if __name__ == "__main__":
    sys.exit(main())
