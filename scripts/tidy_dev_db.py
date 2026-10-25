"""Deletes the players repeated seeding leaves behind in the dev
database.

Deleting a club never deletes players (a player is global), which is
right for the product but leaves every seed run's cast behind. Only
removes a player in no club with no ledger entry, signup, absence or
queue place; anything with a trace is reported and kept.

    uv run python scripts/tidy_dev_db.py          # count them
    uv run python scripts/tidy_dev_db.py --yes    # delete them

`--drop-club "<name>"` removes a whole club, for a seed club stuck
behind a settled season the API rightly refuses to delete.

Deleting requires VOLLEYFLOW_DEV_LOGIN=1 (set by dev-api.ps1, never in
production); the host is printed so you can see which database it is.
"""

from __future__ import annotations

import os
import sys

from sqlalchemy import text
from sqlalchemy.engine import Connection

from volleyflow.db.engine import database_url, get_engine

# Every table that can point at a player, listed so a new one shows up here
# instead of a person being deleted silently.
REFERENCES = [
    ("club_members", "player_id"),
    ("ledger_entries", "player_id"),
    ("season_members", "player_id"),
    ("absences", "player_id"),
    ("drop_ins", "player_id"),
    ("drop_ins", "brought_by_player_id"),
]

UNREFERENCED = "SELECT id FROM players p WHERE " + " AND ".join(
    f"NOT EXISTS (SELECT 1 FROM {table} t WHERE t.{column} = p.id)"
    for table, column in REFERENCES
)


def drop_club(conn: Connection, name: str) -> None:
    """Removes a club and everything under it, children first, table by
    table: the schema has no cascades, so every delete is explicit.
    """
    ids = [
        row[0]
        for row in conn.execute(
            text("SELECT id FROM clubs WHERE name = :name"), {"name": name}
        )
    ]
    if not ids:
        print(f'no club named "{name}"')
        return

    seasons = [
        row[0]
        for row in conn.execute(
            text("SELECT id FROM seasons WHERE club_id = ANY(:ids)"), {"ids": ids}
        )
    ]
    games = [
        row[0]
        for row in conn.execute(
            text("SELECT id FROM games WHERE season_id = ANY(:ids)"), {"ids": seasons}
        )
    ] or [0]
    for statement, params in [
        ("DELETE FROM drop_ins WHERE game_id = ANY(:ids)", {"ids": games}),
        ("DELETE FROM absences WHERE game_id = ANY(:ids)", {"ids": games}),
        ("DELETE FROM games WHERE season_id = ANY(:ids)", {"ids": seasons or [0]}),
        (
            "DELETE FROM season_members WHERE season_id = ANY(:ids)",
            {"ids": seasons or [0]},
        ),
        ("DELETE FROM ledger_entries WHERE club_id = ANY(:ids)", {"ids": ids}),
        ("DELETE FROM seasons WHERE club_id = ANY(:ids)", {"ids": ids}),
        ("DELETE FROM club_members WHERE club_id = ANY(:ids)", {"ids": ids}),
        ("DELETE FROM clubs WHERE id = ANY(:ids)", {"ids": ids}),
    ]:
        conn.execute(text(statement), params)
    print(
        f'dropped club "{name}" (id {", ".join(str(i) for i in ids)}) and its seasons'
    )


def main() -> int:
    host = database_url().split("@")[-1].split("/")[0]
    deleting = "--yes" in sys.argv
    club = None
    if "--drop-club" in sys.argv:
        club = sys.argv[sys.argv.index("--drop-club") + 1]
        deleting = True
    if deleting and os.environ.get("VOLLEYFLOW_DEV_LOGIN") != "1":
        print(f"about to delete from {host}", file=sys.stderr)
        print(
            "Refusing: set VOLLEYFLOW_DEV_LOGIN=1 to confirm this is a "
            "development database.",
            file=sys.stderr,
        )
        return 1

    print(f"database: {host}")
    if club is not None:
        with get_engine().begin() as conn:
            drop_club(conn, club)

    with get_engine().begin() as conn:
        total = conn.execute(text("SELECT count(*) FROM players")).scalar() or 0
        stranded = [row[0] for row in conn.execute(text(UNREFERENCED))]
        kept = (
            conn.execute(
                text(
                    "SELECT count(*) FROM players p WHERE NOT EXISTS "
                    "(SELECT 1 FROM club_members cm WHERE cm.player_id = p.id)"
                )
            ).scalar()
            or 0
        )

        print(f"players: {total}")
        print(f"  in no club: {kept}")
        print(f"  in no club and referenced by nothing at all: {len(stranded)}")
        if kept > len(stranded):
            print(
                f"  ({kept - len(stranded)} of those are left alone — "
                "something still points at them)"
            )

        if not stranded:
            print("nothing to tidy")
            return 0
        if not deleting:
            print("\nre-run with --yes to delete them")
            return 0

        conn.execute(
            text("DELETE FROM players WHERE id = ANY(:ids)"), {"ids": stranded}
        )
        print(f"\ndeleted {len(stranded)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
