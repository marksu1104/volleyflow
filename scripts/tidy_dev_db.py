"""Delete the people that repeated seeding leaves behind in the dev
database.

Deleting a club deliberately never deletes players: a Player is global
and outlives any one club (CLAUDE.md 2.1), so somebody who played in a
club that closed is still the same person with the same ledger history
next season. That is right for the product and wrong for a script that
deletes and rebuilds the same club twenty times in an afternoon — every
run leaves its whole cast behind, attached to nothing.

Found by counting, 2026-09-12: 2,223 players in the dev branch, of which
2,162 belonged to no club at all. Harmless for storage at that size, but
it made a real question ("are there duplicate people in here?")
impossible to answer by looking.

Only ever removes a player who is in no club AND has no ledger entry, no
signup, no absence and no queue place anywhere. Anything with a trace of
having taken part is left alone and reported, because a player row that
money points at is not litter.

    uv run python scripts/tidy_dev_db.py          # count them
    uv run python scripts/tidy_dev_db.py --yes    # delete them

`--drop-club "<name>"` removes a whole club and everything under it,
which `seed_dev.py` cannot always do for itself: the API refuses to
delete a club holding a **settled** season, correctly — those are real
closed books — and anything that settles a season while testing leaves
the seed club stuck, so every later seed run dies on the same refusal.
The product rule stays as it is; this is the dev-only escape hatch for
a database whose whole purpose is to be thrown away.

Deleting needs `VOLLEYFLOW_DEV_LOGIN=1` — the flag this project already
uses to mean "this is a development environment", set by dev-api.ps1 and
never set in production. A first attempt tried to recognise the dev
branch by its hostname; Neon names every branch something like
`ep-dawn-pine-azbsvr6x`, so there is nothing in the URL to recognise, and
a guard that cannot tell the two apart is worse than no guard because it
reads like one. The host is printed instead, so the person running it can
see which database they are about to tidy.
"""

from __future__ import annotations

import os
import sys

from sqlalchemy import text
from sqlalchemy.engine import Connection

from volleyflow.db.engine import database_url, get_engine

# Every table that can point at a player. Listed rather than derived, so
# adding one to the schema without thinking about this file shows up as a
# failing count here rather than as a silently deleted person.
REFERENCES = [
    ("club_members", "player_id"),
    ("ledger_entries", "player_id"),
    ("season_members", "player_id"),
    ("absences", "player_id"),
    ("drop_ins", "player_id"),
    ("drop_ins", "brought_by_player_id"),
    ("waitlist_entries", "player_id"),
]

UNREFERENCED = "SELECT id FROM players p WHERE " + " AND ".join(
    f"NOT EXISTS (SELECT 1 FROM {table} t WHERE t.{column} = p.id)"
    for table, column in REFERENCES
)


def drop_club(conn: Connection, name: str) -> None:
    """Removes a club and everything hanging off it, children first.

    Written out table by table rather than leaning on cascades, because
    the schema deliberately has none: every delete in the product is an
    explicit one so that "what does removing this take with it" is a
    question the code answers rather than the database.
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
        ("DELETE FROM waitlist_entries WHERE game_id = ANY(:ids)", {"ids": games}),
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
