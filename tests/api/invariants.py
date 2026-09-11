"""What has to be true of a season, however it was churned.

Split out of test_chaos.py when the random sweep in test_fuzz.py needed
the same answers. Every rule here is written as "find the problems" and
returns a list rather than asserting, so a caller can report all of them
at once alongside the sequence of operations that produced them — a fuzz
failure is useless without the recipe.

The structural rules are cheap (one GET) and run after every single
operation. The money rules cost a request per player, so they run once at
the end of a run.
"""

from decimal import Decimal
from typing import Any

from fastapi.testclient import TestClient


def on_court(detail: dict[str, Any], game: dict[str, Any]) -> list[str]:
    """Everyone taking up a slot that night, by name: the members who
    haven't taken leave, plus whoever is confirmed as a drop-in."""
    absent = {a["player_name"] for a in game["absences"]}
    return [m["name"] for m in detail["members"] if m["name"] not in absent] + [
        d["player_name"] for d in game["confirmed_drop_ins"]
    ]


def game_problems(detail: dict[str, Any], game: dict[str, Any]) -> list[str]:
    """The rules that must hold for one game whatever was done to it."""
    playing = on_court(detail, game)
    queued = [w["player_name"] for w in game["waitlist_entries"]]
    where = f"game {game['id']} ({game['date']})"
    problems = []

    if len(playing) != len(set(playing)):
        problems.append(f"{where}: somebody is on court twice — {playing}")
    both = set(playing) & set(queued)
    if both:
        problems.append(f"{where}: on court and waiting at once — {sorted(both)}")
    if len(queued) != len(set(queued)):
        problems.append(f"{where}: queued twice — {queued}")
    if len(playing) > detail["capacity"]:
        absent = sorted(a["player_name"] for a in game["absences"])
        problems.append(
            f"{where}: {len(playing)} people for {detail['capacity']} slots\n"
            f"    on court: {playing}\n"
            f"    roster:   {[m['name'] for m in detail['members']]}\n"
            f"    away:     {absent}\n"
            f"    queued:   {queued}"
        )

    # An absence is refunded only when somebody actually fills the slot,
    # so `filled_by` is the billing fact and it has to name a real person
    # who is on this game's list. A stale name here is a refund for a slot
    # nobody paid for — see docs/billing-rules.md.
    confirmed = {d["player_name"] for d in game["confirmed_drop_ins"]}
    for absence in game["absences"]:
        filler = absence.get("filled_by")
        if filler is not None and filler not in confirmed:
            problems.append(
                f"{where}: {absence['player_name']} is refunded against {filler}, "
                "who is not on the list"
            )
        arranged = absence.get("covered_by")
        if arranged is not None and arranged not in confirmed:
            problems.append(
                f"{where}: {absence['player_name']}'s 代打 {arranged} "
                "is not on the list"
            )

    # A drop-in saying they cover somebody must be covering a real absence
    # in the same game — the other direction of the same relationship.
    absent_names = {a["player_name"] for a in game["absences"]}
    for drop_in in game["confirmed_drop_ins"]:
        covering = drop_in.get("covering")
        if covering is not None and covering not in absent_names:
            problems.append(
                f"{where}: {drop_in['player_name']} stands in for {covering}, "
                "who is not away"
            )

    return problems


def structural_problems(client: TestClient, season_id: int) -> list[str]:
    """Every game in the season, in one request."""
    detail = client.get(f"/seasons/{season_id}").json()
    problems = []
    for game in detail["games"]:
        if game["status"] == "cancelled":
            continue
        problems.extend(game_problems(detail, game))
    return problems


def money_problems(client: TestClient, club_id: int, season_id: int) -> list[str]:
    """The rules about who owes what. A request per player, so this is for
    the end of a run rather than after every step."""
    detail = client.get(f"/seasons/{season_id}").json()
    balances = client.get(f"/clubs/{club_id}/balances?season_id={season_id}").json()
    members = {m["id"] for m in detail["members"]}

    ever_played = set()
    for game in detail["games"]:
        if game["status"] == "cancelled":
            continue
        ever_played.update(d["player_id"] for d in game["confirmed_drop_ins"])

    problems = []
    for row in balances:
        player_id = row["player_id"]
        # Somebody who joined nothing and left again owes nothing. This is
        # the money version of the churn rules above: every signup that
        # was undone must have taken its charge with it.
        never_on_court = player_id not in members and player_id not in ever_played
        if never_on_court and Decimal(row["balance"]) != 0:
            problems.append(
                f"player {player_id} played no game but their balance is "
                f"{row['balance']}"
            )
        # The two endpoints that report money must agree. The balances
        # query adds up in SQL; the ledger adds up in Python.
        ledger = client.get(f"/clubs/{club_id}/players/{player_id}/ledger").json()
        if Decimal(ledger["balance"]) != Decimal(row["balance"]):
            problems.append(
                f"player {player_id}: ledger says {ledger['balance']}, "
                f"balances says {row['balance']}"
            )

    return problems
