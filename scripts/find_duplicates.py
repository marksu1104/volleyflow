"""Report people who appear more than once in the same club.

Read-only. Touches nothing, asks for no confirmation, and is safe to run
against production — which is the point: the rule in this project is that
production credentials are never handled from a development session, so
this is the shape a production question has to take. Whoever holds the
connection string runs it and reads the answer.

    # against whatever DATABASE_URL is set to (the dev branch by default)
    uv run python scripts/find_duplicates.py

    # against production, from a shell where that is what DATABASE_URL says
    uv run python scripts/find_duplicates.py

Why it exists: until 2026-09-12, two overlapping "add this person to the
season" requests could each look for a name, each find nothing, and each
create their own Player row — silently, with no error, and with **both**
of them charged a full season fee. The race is fixed (add_member now
locks the season row), but any duplicate it already made is still there,
and a duplicate that two different ledgers point at cannot be merged by
the app: `link_player` deliberately refuses to fold together two people
who both have money.

So this answers three questions, in order of how much they matter:

  1. which names occur twice in one club at all;
  2. for each of those rows, whether anything actually points at it —
     a ledger entry, a signup, an absence, a season roster place;
  3. therefore which are harmless (one real person, one empty row that
     can be ignored or tidied) and which are a real split identity that
     needs a human decision about the money.

It prints, and stops. Deciding what to do about a split identity is not
a script's business.
"""

from __future__ import annotations

from sqlalchemy import text

from volleyflow.db.engine import database_url, get_engine

DUPLICATE_NAMES = """
SELECT cm.club_id, c.name AS club, p.name AS person, count(*) AS copies
FROM club_members cm
JOIN players p ON p.id = cm.player_id
JOIN clubs c ON c.id = cm.club_id
GROUP BY cm.club_id, c.name, p.name
HAVING count(*) > 1
ORDER BY c.name, p.name
"""

COPIES = """
SELECT p.id,
       p.line_user_id IS NOT NULL AS linked,
       (SELECT count(*) FROM ledger_entries l WHERE l.player_id = p.id) AS ledger,
       (SELECT coalesce(sum(l.amount), 0) FROM ledger_entries l
         WHERE l.player_id = p.id AND l.club_id = :club_id) AS balance,
       (SELECT count(*) FROM season_members sm WHERE sm.player_id = p.id) AS rosters,
       (SELECT count(*) FROM drop_ins d WHERE d.player_id = p.id) AS signups,
       (SELECT count(*) FROM absences a WHERE a.player_id = p.id) AS absences
FROM club_members cm
JOIN players p ON p.id = cm.player_id
WHERE cm.club_id = :club_id AND p.name = :person
ORDER BY p.id
"""


def main() -> int:
    print(f"database: {database_url().split('@')[-1].split('/')[0]}\n")
    with get_engine().connect() as conn:
        groups = list(conn.execute(text(DUPLICATE_NAMES)))
        if not groups:
            print("No name appears twice in any club. Nothing to look at.")
            return 0

        needs_a_decision = 0
        for group in groups:
            print(f'"{group.person}" in {group.club} — {group.copies} rows')
            busy = 0
            for row in conn.execute(
                text(COPIES), {"club_id": group.club_id, "person": group.person}
            ):
                traces = []
                if row.ledger:
                    traces.append(
                        f"{row.ledger} ledger entries (balance {row.balance})"
                    )
                if row.rosters:
                    traces.append(f"on {row.rosters} season rosters")
                if row.signups:
                    traces.append(f"{row.signups} signups")
                if row.absences:
                    traces.append(f"{row.absences} absences")
                if row.linked:
                    traces.append("has a LINE account")
                if traces:
                    busy += 1
                summary = ", ".join(traces) or "nothing points at this row"
                print(f"    id {row.id}: {summary}")

            if busy > 1:
                needs_a_decision += 1
                print(
                    "    -> a real split identity: more than one of these rows "
                    "carries history, so merging them is a decision about money"
                )
            else:
                print(
                    "    -> harmless: only one of these rows is real, "
                    "the rest are empty"
                )
            print()

        print(
            f"{len(groups)} duplicated names, {needs_a_decision} of which "
            "need a decision.\n"
        )
        print(
            "Read these, don't act on the count. Two different people can "
            "share a name, and a guest typed in by hand twice is two rows on "
            "purpose — signing somebody up by bare name always creates a new "
            "person, which is why the app offers a picker of people you have "
            "brought before. What is worth looking at is a name where the "
            "*same* human clearly has their money in two places."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
