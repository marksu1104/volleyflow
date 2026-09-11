"""Dumps every row in the database to one JSON file.

The restore drill this exists for: "Neon keeps backups; nobody has ever
tried restoring one" (docs/backlog.md). Neon's own point-in-time restore
is the first line of defence and needs no script — but on the free plan
it only reaches 6 hours back (and only 1GB of changes), which is not
long enough for a mistake found the next day. This is the second line:
a plain logical dump, restorable with restore_db.py regardless of how
long ago it was taken.

Reads every table from the ORM's own metadata (volleyflow.db.models.Base)
rather than reflecting the schema at runtime, so what gets dumped is
exactly what the models say exists — no drift between the two.

    uv run python scripts/backup_db.py                    # ./backups/
    uv run python scripts/backup_db.py --out somewhere.json

DATABASE_URL decides which database this reads — .env's is the Neon dev
branch; production is only ever reached through Render's own environment,
never from here. This only reads; see restore_db.py for writing back.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, time
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from volleyflow.db.engine import database_url, get_engine
from volleyflow.db.models import Base


def _json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return {"__decimal__": str(value)}
    if isinstance(value, datetime):
        return {"__datetime__": value.isoformat()}
    if isinstance(value, date):
        return {"__date__": value.isoformat()}
    if isinstance(value, time):
        return {"__time__": value.isoformat()}
    if isinstance(value, Enum):
        return value.value
    raise TypeError(f"Don't know how to back up a {type(value).__name__}")


def dump(out_path: Path) -> None:
    engine = get_engine()
    host = urlsplit(database_url()).hostname
    print(f"backing up from {host}")

    data: dict[str, list[dict[str, Any]]] = {}
    with engine.connect() as conn:
        # Base.metadata.sorted_tables is topologically ordered — parents
        # before the children that foreign-key to them. Dumping in that
        # order is what lets restore_db.py insert in the same order
        # without disabling foreign key checks.
        for table in Base.metadata.sorted_tables:
            rows = [dict(row._mapping) for row in conn.execute(table.select())]
            data[table.name] = rows
            print(f"  {table.name}: {len(rows)} rows")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(data, ensure_ascii=False, default=_json_default, indent=1),
        encoding="utf-8",
    )
    total = sum(len(rows) for rows in data.values())
    print(f"wrote {total} rows across {len(data)} tables to {out_path}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Output file (default: backups/volleyflow-<timestamp>.json)",
    )
    args = parser.parse_args()

    out_path = args.out or Path("backups") / (
        f"volleyflow-{datetime.now():%Y%m%d-%H%M%S}.json"
    )
    dump(out_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
