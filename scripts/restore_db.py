"""Restores a database from a backup_db.py dump. Destructive.

Every row currently in the database is deleted and replaced with what
the dump file holds — this is a *restore*, not a merge. Refuses to run
without --yes, and prints which host it's about to do this to before it
does anything, since DATABASE_URL is the only thing standing between
"restore the dev branch" and "restore production."

    uv run python scripts/restore_db.py backups/volleyflow-20260912-090000.json --yes

See backup_db.py for the dump format this reads.
"""

from __future__ import annotations

import argparse
import base64
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
    # The other half of backup_db's bytes encoding. One without the other
    # is worse than neither: a backup that writes and then can't be
    # restored is only discovered on the day it's needed.
    if "__bytes__" in obj:
        return base64.b64decode(obj["__bytes__"])
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


def _defer_self_references(table: Any, rows: list[dict[str, Any]]) -> list[Any]:
    """Empties the columns that point back into this same table, and
    returns the updates that fill them in again after the insert.

    `sorted_tables` orders the tables against each other; nothing orders
    the rows *within* one. So a ledger entry that reverses another one
    can be handed to Postgres before the entry it reverses exists, and
    the foreign key refuses it. Found on 2026-09-16 by the restore
    drill, against a dev branch that had an undone payment in it: the
    backup wrote perfectly well and could not be read back, which is
    precisely the failure a backup exists to prevent.

    Nulled and refilled rather than sorted into a safe order: an order
    only exists while the references form no cycle, and nothing about a
    self-reference promises that.
    """
    columns = [fk.parent.name for fk in table.foreign_keys if fk.column.table is table]
    if not columns:
        return []

    key = list(table.primary_key.columns)[0]
    updates: list[Any] = []
    for row in rows:
        pointing = {name: row[name] for name in columns if row.get(name) is not None}
        if not pointing:
            continue
        for name in pointing:
            row[name] = None
        updates.append(table.update().where(key == row[key.name]).values(**pointing))
    return updates


def _from_before_one_signup_table(data: dict[str, list[dict[str, Any]]]) -> None:
    """Reads a dump taken before 2026-10-07, when the queue was a table of
    its own, into today's shape: each queued row becomes a queued signup
    in drop_ins, and a drop-in's `from_waitlist_at` is its `queued_at`.

    Restoring goes table by table from today's models, so without this
    an older dump would restore every table it still recognises and
    silently skip `waitlist_entries` — everybody who was waiting, gone,
    from the one script whose job is to lose nothing. In place.
    """
    queue = data.pop("waitlist_entries", None)
    drop_ins = data.setdefault("drop_ins", [])
    for row in drop_ins:
        if "from_waitlist_at" in row:
            row["queued_at"] = row.pop("from_waitlist_at")
        row.setdefault("status", "playing")
    if not queue:
        return
    next_id = max((row["id"] for row in drop_ins), default=0) + 1
    for i, row in enumerate(queue):
        drop_ins.append(
            {
                "id": next_id + i,
                "player_id": row["player_id"],
                "game_id": row["game_id"],
                "signed_up_at": row["queued_at"],
                "cancelled_at": None,
                "status": "queued",
                "queued_at": row["queued_at"],
                "absorbed_at": None,
                "covers_absence_id": None,
                "brought_by_player_id": row.get("brought_by_player_id"),
                "charged_amount": None,
            }
        )


def restore(dump_path: Path) -> None:
    data: dict[str, list[dict[str, Any]]] = json.loads(
        dump_path.read_text(encoding="utf-8"), object_hook=_json_object_hook
    )
    _from_before_one_signup_table(data)

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
                deferred = _defer_self_references(table, rows)
                conn.execute(table.insert(), rows)
                for update in deferred:
                    conn.execute(update)
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
