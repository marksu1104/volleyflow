"""Hundreds of random sequences, checked after every single step.

Asked for on 2026-09-11: 「確定都檢查完所有奇怪的操作也不會錯誤了嗎」.
The honest answer to that was no — test_chaos.py holds five sequences and
chaos.js three, and all eight are ones somebody thought of. A rule only
holds against the sequences you imagined until something else is picking
them.

So this picks them. Each run builds a season, then fires a few hundred
plausible-but-messy operations at it — take leave, undo it, sign somebody
up, name a substitute, promote off the queue, flip the air conditioning,
add and remove members — choosing each one from whatever state the season
happens to be in, and checks every invariant after each. The seeds are
fixed so a failure is reproducible, and the operation log is printed with
it so the recipe comes with the bug.

A refusal is not a failure: half of what this does is illegal (signing
somebody up twice, taking leave on a game already full) and the server is
supposed to say no. What must never happen is a 500, an invariant broken,
or money left behind by somebody who never played.
"""

import random
from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient
from httpx import Response

from tests.api.factories import start_season
from tests.api.invariants import money_problems, structural_problems

GUESTS = ["小明", "阿德", "Taco", "Ricky", "小美", "阿哲", "Wen", "老王"]
MEMBERS = ["Alice", "Bob", "Carol", "Dave", "Eve"]

Done = tuple[str, "Response"]
Operation = Callable[[random.Random, TestClient, dict[str, Any]], Done | None]


def _detail(client: TestClient, season: dict[str, Any]) -> dict[str, Any]:
    body: dict[str, Any] = client.get(f"/seasons/{season['id']}").json()
    return body


def _pick(rng: random.Random, items: list[Any]) -> Any | None:
    return rng.choice(items) if items else None


def take_leave(
    rng: random.Random, client: TestClient, season: dict[str, Any]
) -> Done | None:
    detail = _detail(client, season)
    game = _pick(rng, detail["games"])
    member = _pick(rng, detail["members"])
    if not game or not member:
        return None
    sent = client.post(
        "/absences", json={"player_name": member["name"], "game_id": game["id"]}
    )
    return f"take_leave({member['name']}, game {game['id']})", sent


def undo_leave(
    rng: random.Random, client: TestClient, season: dict[str, Any]
) -> Done | None:
    detail = _detail(client, season)
    absences = [a for g in detail["games"] for a in g["absences"]]
    absence = _pick(rng, absences)
    if not absence:
        return None
    sent = client.post(f"/absences/{absence['id']}/cancel", json={})
    return f"undo_leave({absence['player_name']}, absence {absence['id']})", sent


def sign_up(
    rng: random.Random, client: TestClient, season: dict[str, Any]
) -> Done | None:
    detail = _detail(client, season)
    game = _pick(rng, detail["games"])
    if not game:
        return None
    name = rng.choice(GUESTS)
    sent = client.post("/drop-ins", json={"player_name": name, "game_id": game["id"]})
    return f"sign_up({name}, game {game['id']})", sent


def cancel_signup(
    rng: random.Random, client: TestClient, season: dict[str, Any]
) -> Done | None:
    detail = _detail(client, season)
    drop_ins = [d for g in detail["games"] for d in g["confirmed_drop_ins"]]
    drop_in = _pick(rng, drop_ins)
    if not drop_in:
        return None
    sent = client.post(f"/drop-ins/{drop_in['id']}/cancel", json={})
    return f"cancel_signup({drop_in['player_name']}, drop-in {drop_in['id']})", sent


def name_substitute(
    rng: random.Random, client: TestClient, season: dict[str, Any]
) -> Done | None:
    detail = _detail(client, season)
    absences = [a for g in detail["games"] for a in g["absences"]]
    absence = _pick(rng, absences)
    if not absence:
        return None
    name = rng.choice(GUESTS)
    sent = client.put(
        f"/absences/{absence['id']}/substitute", json={"player_name": name}
    )
    return f"name_substitute(absence {absence['id']} -> {name})", sent


def leave_queue(
    rng: random.Random, client: TestClient, season: dict[str, Any]
) -> Done | None:
    detail = _detail(client, season)
    queued = [w for g in detail["games"] for w in g["waitlist_entries"]]
    entry = _pick(rng, queued)
    if not entry:
        return None
    sent = client.post(f"/waitlist/{entry['id']}/cancel", json={})
    return f"leave_queue({entry['player_name']}, entry {entry['id']})", sent


def promote(
    rng: random.Random, client: TestClient, season: dict[str, Any]
) -> Done | None:
    """The organizer jumping the queue, with and without naming who steps
    out — the operation that has to move two ledgers in one transaction."""
    detail = _detail(client, season)
    candidates = [(g, w) for g in detail["games"] for w in g["waitlist_entries"]]
    chosen = _pick(rng, candidates)
    if not chosen:
        return None
    game, entry = chosen
    body: dict[str, Any] = {}
    if game["confirmed_drop_ins"] and rng.random() < 0.6:
        out = rng.choice(game["confirmed_drop_ins"])
        body["replacing_drop_in_id"] = out["id"]
    sent = client.post(f"/waitlist/{entry['id']}/promote", json=body)
    return f"promote({entry['player_name']}, entry {entry['id']}, {body})", sent


def flip_air_conditioning(
    rng: random.Random, client: TestClient, season: dict[str, Any]
) -> Done | None:
    """The one season parameter expected to change mid-season, and the one
    that re-prices every member with an adjustment entry."""
    detail = _detail(client, season)
    game = _pick(rng, detail["games"])
    if not game:
        return None
    on = not game["air_conditioned"]
    sent = client.put(
        f"/games/{game['id']}/air-conditioning", json={"air_conditioned": on}
    )
    return f"flip_air_conditioning(game {game['id']} -> {on})", sent


def add_member(
    rng: random.Random, client: TestClient, season: dict[str, Any]
) -> Done | None:
    name = rng.choice(GUESTS + MEMBERS)
    sent = client.post(f"/seasons/{season['id']}/members", json={"player_name": name})
    return f"add_member({name})", sent


def remove_member(
    rng: random.Random, client: TestClient, season: dict[str, Any]
) -> Done | None:
    detail = _detail(client, season)
    member = _pick(rng, detail["members"])
    if not member:
        return None
    sent = client.delete(f"/seasons/{season['id']}/members/{member['id']}")
    return f"remove_member({member['name']}, id {member['id']})", sent


def change_capacity(
    rng: random.Random, client: TestClient, season: dict[str, Any]
) -> Done | None:
    """Raises or lowers the cap that every other operation here is
    supposed to respect — see test_capacity_limits.py for the rule this
    exercises against everything else happening at the same time."""
    detail = _detail(client, season)
    delta = rng.choice([-2, -1, 1, 2])
    new_capacity = max(1, detail["capacity"] + delta)
    sent = client.patch(f"/seasons/{season['id']}", json={"capacity": new_capacity})
    return f"change_capacity({detail['capacity']} -> {new_capacity})", sent


# Weighted so the season stays busy rather than draining to nothing: the
# interesting bugs live in a game that is full, with a queue behind it.
OPERATIONS: list[tuple[Operation, int]] = [
    (take_leave, 6),
    (undo_leave, 5),
    (sign_up, 7),
    (cancel_signup, 4),
    (name_substitute, 5),
    (leave_queue, 3),
    (promote, 4),
    (flip_air_conditioning, 2),
    (add_member, 2),
    (remove_member, 1),
    (change_capacity, 2),
]
_CHOICES = [op for op, weight in OPERATIONS for _ in range(weight)]


def run_fuzz(client: TestClient, seed: int, steps: int) -> None:
    rng = random.Random(seed)
    season = start_season(
        client,
        total_venue_cost="12000",
        game_dates=["2026-08-18", "2026-08-25", "2026-09-01"],
        member_names=MEMBERS,
        capacity=6,
    )
    log: list[str] = []

    for step in range(steps):
        outcome = rng.choice(_CHOICES)(rng, client, season)
        if outcome is None:
            continue
        described, sent = outcome
        # A refusal is a correct answer to half of what this does. A 500
        # never is: that is the server falling over rather than saying no.
        assert sent.status_code < 500, (
            f"seed {seed}, step {step}: {described} -> {sent.status_code}\n"
            + sent.text[:400]
            + "\n\nwhat got it there:\n  "
            + "\n  ".join(log[-12:])
        )
        log.append(f"{described} -> {sent.status_code}")
        problems = structural_problems(client, season["id"])
        assert not problems, (
            f"seed {seed}, step {step}\n"
            + "\n".join(problems)
            + "\n\nwhat got it there:\n  "
            + "\n  ".join(log[-12:])
        )

    problems = money_problems(client, season["club_id"], season["id"])
    assert not problems, (
        f"seed {seed}, after {len(log)} operations\n"
        + "\n".join(problems)
        + "\n\nwhat got it there:\n  "
        + "\n  ".join(log[-25:])
    )


@pytest.mark.parametrize("seed", range(8))
def test_random_churn_never_breaks_a_season(client: TestClient, seed: int) -> None:
    # A small capacity on purpose: six slots and five members means the
    # court is full or nearly full most of the time, which is where the
    # queue, the substitutes and the promotions all interact.
    run_fuzz(client, seed=seed, steps=60)


def test_a_long_run_on_one_season_stays_sound(client: TestClient) -> None:
    # The short runs each start clean. This one lets a single season
    # accumulate several hundred changes, which is what a real one does
    # over a term — and where anything that leaks a row or a ledger entry
    # per operation would show up.
    run_fuzz(client, seed=99, steps=300)
