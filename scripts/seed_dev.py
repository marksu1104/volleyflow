"""Fills the dev database with a club that looks like a real one: a
roster, games, leave, substitutes, a queue, and money owed in three
directions.

Goes through the HTTP API, not the database, so every ledger entry is
written by the real route handlers rather than a copy of their logic.
That also makes it a smoke test of the endpoints the app needs.

    VOLLEYFLOW_DEV_LOGIN=1 uv run uvicorn volleyflow.api.main:app --port 8000
    uv run python scripts/seed_dev.py

Then open http://localhost:5500/member.html?as=周恆

Runs only against a local server, and deletes only the club it created
(CLUB_NAME).
"""

from __future__ import annotations

import sys
from datetime import date, timedelta
from typing import Any
from urllib.parse import quote

import httpx

API = "http://localhost:8000"
CLUB_NAME = "測試球隊（seed）"

ORGANIZER = "周恆"
MEMBERS = [
    ("林書妤", "female"),
    ("翔", "male"),
    ("芷若", "female"),
    ("阿Joy", "female"),
    ("Jason", "male"),
    ("Momo", "male"),
    ("WenChiao", "female"),
    ("Kevin", "male"),
    ("Nathan", "male"),
    ("Lorenzo", "female"),
    ("詠晴", "male"),
    ("胖虎", "female"),
    ("承恩", "male"),
    ("俊宏", "male"),
    ("冠廷", "male"),
    ("品妍", "female"),
    ("宗翰", "male"),
]

# Realistic figures: 13 games, 8 of them cooled, 52290 in total, 540 a night
# for the air conditioning, which prices at exactly 235 cooled and 205
# plain.
TOTAL_VENUE_COST = "52290"
AC_SURCHARGE = "540"
COOLED_GAMES = 8
TOTAL_GAMES = 13


def token_for(name: str) -> dict[str, str]:
    """The dev-login header; the name is percent-encoded because header
    values are ASCII.
    """
    return {"Authorization": f"Bearer dev:{quote(name)}"}


def call(
    method: str, path: str, as_name: str, body: dict[str, Any] | None = None
) -> Any:
    response = httpx.request(
        method, API + path, headers=token_for(as_name), json=body, timeout=30
    )
    if response.status_code >= 400:
        raise SystemExit(f"{method} {path} -> {response.status_code} {response.text}")
    return response.json() if response.content else None


def join(club_id: int, name: str) -> None:
    """Joins the way a real person does, with the club's invite link."""
    token = call("GET", f"/clubs/{club_id}/invite", ORGANIZER)["token"]
    member = call("POST", f"/clubs/{club_id}/join", name, {"invite": token})
    call(
        "POST",
        f"/clubs/{club_id}/members/{member['id']}/approve",
        ORGANIZER,
        {"as_fixed": False},
    )


def sign_in(name: str) -> dict[str, Any]:
    body = {"id_token": f"dev:{quote(name)}", "display_name": name}
    response = httpx.post(API + "/players/identify", json=body, timeout=30)
    if response.status_code >= 400:
        raise SystemExit(
            "Could not sign in. Is the server running with "
            f"VOLLEYFLOW_DEV_LOGIN=1? ({response.status_code} {response.text})"
        )
    result: dict[str, Any] = response.json()
    return result


def tuesdays_from(start: date, count: int) -> list[date]:
    while start.weekday() != 1:
        start += timedelta(days=1)
    return [start + timedelta(weeks=i) for i in range(count)]


def main() -> int:
    if not API.startswith("http://localhost"):
        raise SystemExit("This only ever runs against a local server.")

    print(f"signing in as {ORGANIZER}")
    sign_in(ORGANIZER)

    # Start clean, removing only this script's own club.
    for club in call("GET", "/clubs", ORGANIZER):
        if club["name"] == CLUB_NAME:
            print(f"removing the previous {CLUB_NAME} (id {club['id']})")
            call("DELETE", f"/clubs/{club['id']}", ORGANIZER)

    club = call("POST", "/clubs", ORGANIZER, {"name": CLUB_NAME})
    club_id = club["id"]
    print(f"created club {club_id}")

    # Half the season past and half to come.
    dates = tuesdays_from(date.today() - timedelta(weeks=6), TOTAL_GAMES)
    season = call(
        "POST",
        f"/clubs/{club_id}/seasons",
        ORGANIZER,
        {
            "total_venue_cost": TOTAL_VENUE_COST,
            "ac_surcharge": AC_SURCHARGE,
            "game_dates": [d.isoformat() for d in dates],
            "air_conditioned_dates": [d.isoformat() for d in dates[:COOLED_GAMES]],
            "member_names": [ORGANIZER] + [name for name, _ in MEMBERS],
            "capacity": 18,
            "minimum_roster": 12,
            "game_start_time": "18:30",
            "game_end_time": "22:00",
            "location": "晴光館",
            "change_deadline_hours": 24,
        },
    )
    season_id = season["id"]
    print(f"created season {season_id}: {TOTAL_GAMES} games, 18 members")

    detail = call("GET", f"/seasons/{season_id}", ORGANIZER)
    by_name = {m["name"]: m["id"] for m in detail["members"]}
    games = detail["games"]

    for name, gender in MEMBERS:
        if gender:
            call(
                "PUT",
                f"/players/{by_name[name]}/gender",
                ORGANIZER,
                {"gender": gender},
            )

    # Leave, with a substitute — the only kind that gets refunded.
    covered = call(
        "POST",
        "/absences",
        ORGANIZER,
        {"player_name": "林書妤", "game_id": games[7]["id"]},
    )
    call(
        "PUT",
        f"/absences/{covered['id']}/substitute",
        ORGANIZER,
        {"player_name": "小明", "gender": "male"},
    )
    print("林書妤 took leave, covered by 小明 — refunded")

    # Leave with nobody covering — not refunded, and a gap in the roster.
    call(
        "POST", "/absences", ORGANIZER, {"player_name": "翔", "game_id": games[8]["id"]}
    )
    print("翔 took leave, uncovered — not refunded")

    # A club member not on the roster brings a friend: the ordinary drop-in,
    # with a brought-by name on the money screen. Signing in creates a new
    # player and gives no membership; joining is a separate step.
    visitor = "阿凱"
    sign_in(visitor)
    join(club_id, visitor)
    call(
        "POST",
        f"/games/{games[8]['id']}/drop-ins",
        visitor,
        {
            "people": [
                {"player_name": visitor, "player_id": None, "gender": "male"},
                {"player_name": f"{visitor}的朋友", "gender": "female"},
            ]
        },
    )
    print(f"{visitor} joined the club and signed up with a guest")

    # Two roster entries get a LINE-style identity of their own, so the ?as=
    # links printed at the end work: sign in, join, then the organizer links
    # the two, which also exercises that endpoint.
    for name in ("林書妤", "詠晴"):
        identity = sign_in(name)
        join(club_id, name)
        call(
            "POST",
            f"/clubs/{club_id}/players/{by_name[name]}/link",
            ORGANIZER,
            {"line_player_id": identity["id"]},
        )
    print("林書妤 and 詠晴 can sign in as themselves")

    # The organizer brings the same friend a few times, so the quick-pick
    # list has entries. A member takes leave first each night, since the
    # roster fills the court and a queued signup records no bringer.
    for game, absentee in zip(games[3:6], ["Kevin", "Nathan", "Momo"], strict=True):
        call(
            "POST",
            "/absences",
            ORGANIZER,
            {"player_name": absentee, "game_id": game["id"]},
        )
        call(
            "POST",
            f"/games/{game['id']}/drop-ins",
            ORGANIZER,
            {"people": [{"player_name": "老王", "gender": "male"}]},
        )
    print(f"{ORGANIZER} has brought 老王 three times — quick-pick has entries")

    # One night's forecast was wrong: turning it off corrects every member's
    # charge with an adjustment entry.
    call(
        "PUT",
        f"/games/{games[2]['id']}/air-conditioning",
        ORGANIZER,
        {"air_conditioned": False},
    )
    print(f"{games[2]['date']} ran without the air conditioning — everyone credited")

    # Money in three directions: paid up, part paid, overpaid.
    balances = {
        b["player_id"]: b
        for b in call(
            "GET", f"/clubs/{club_id}/balances?season_id={season_id}", ORGANIZER
        )
    }
    settled_in_full = ["翔", "芷若", "阿Joy", "Jason", "Momo"]
    part_paid = ["林書妤"]
    overpaid = ["詠晴"]

    for name in settled_in_full + part_paid + overpaid:
        owed = -float(balances.get(by_name[name], {}).get("balance", 0))
        if owed <= 0:
            continue
        amount = (
            owed
            if name in settled_in_full
            else owed / 2
            if name in part_paid
            else owed + 200
        )
        call(
            "POST",
            f"/clubs/{club_id}/players/{by_name[name]}/payments",
            ORGANIZER,
            {
                "amount": str(int(amount)),
                "season_id": season_id,
                "note": "seed",
                "client_token": f"seed-{name}",
            },
        )
    print(
        f"recorded payments: {len(settled_in_full)} settled, 1 part paid, 1 in credit"
    )

    print()
    print("done. with the frontend served from frontend/ on port 5500:")
    for name, note in [
        (ORGANIZER, "organizer"),
        ("林書妤", "owes money, took leave"),
        ("詠晴", "in credit"),
    ]:
        print(f"  http://localhost:5500/member.html?as={quote(name)}   ({note})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
