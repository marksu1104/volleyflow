"""Dumps every row in the database to one JSON file.

A second line of defence after Neon's point-in-time restore, which on
the free plan reaches only 6 hours back: a plain dump that
restore_db.py can restore however old it is. Tables come from the
ORM's own metadata, so the dump is exactly what the models define.

    uv run python scripts/backup_db.py                    # ./backups/
    uv run python scripts/backup_db.py --out somewhere.json

Reads whichever database DATABASE_URL names (.env holds the dev
branch). Read-only; restore_db.py writes back.
"""

from __future__ import annotations

import argparse
import base64
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
    if isinstance(value, bytes):
        # Screenshots on problem reports are the one binary column.
        return {"__bytes__": base64.b64encode(value).decode("ascii")}
    raise TypeError(f"Don't know how to back up a {type(value).__name__}")


def dump(out_path: Path) -> None:
    engine = get_engine()
    host = urlsplit(database_url()).hostname
    print(f"backing up from {host}")

    data: dict[str, list[dict[str, Any]]] = {}
    with engine.connect() as conn:
        # Parents before children, so restore_db.py can insert in the same
        # order without disabling foreign keys.
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
