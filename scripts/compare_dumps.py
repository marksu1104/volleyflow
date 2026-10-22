"""Compares two backup_db.py dumps table by table, printing only whether
each table matches.

The last step of the production restore drill
(.github/workflows/restore-drill.yml): production is dumped, restored
into an empty database built by the migrations, dumped again, and the two
dumps compared here. Every row and every column has to survive the round
trip — a count would miss a value restored wrong, which is how the enum
and self-reference faults in restore_db.py would have looked.

Says nothing about the rows themselves, not even how many there are: the
drill runs on GitHub's runners, and this repository's logs are public.

    uv run python scripts/compare_dumps.py before.json after.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

Dump = dict[str, list[dict[str, Any]]]


def _canonical(rows: list[dict[str, Any]]) -> list[str]:
    # Neither dump orders its rows (backup_db.py selects without ORDER
    # BY), so the comparison can't depend on order either.
    return sorted(json.dumps(row, sort_keys=True, ensure_ascii=False) for row in rows)


def differing_tables(before: Dump, after: Dump) -> list[str]:
    """Names of the tables whose rows differ, including a table only one
    of the two dumps has."""
    names = sorted(set(before) | set(after))
    return [
        name
        for name in names
        if name not in before
        or name not in after
        or _canonical(before[name]) != _canonical(after[name])
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("before", type=Path, help="The dump that was restored")
    parser.add_argument("after", type=Path, help="A dump of the restored database")
    args = parser.parse_args(argv)

    before: Dump = json.loads(args.before.read_text(encoding="utf-8"))
    after: Dump = json.loads(args.after.read_text(encoding="utf-8"))
    differing = differing_tables(before, after)

    for name in sorted(set(before) | set(after)):
        print(f"  {name}: {'DIFFERENT' if name in differing else 'identical'}")
    if differing:
        print(f"{len(differing)} table(s) did not survive the round trip")
        return 1
    print("every table survived the round trip")
    return 0


if __name__ == "__main__":
    sys.exit(main())
