"""Fill the dev database with a club that looks like a real one.

The dev branch is empty, so opening the app locally shows "no club yet"
and there is nothing to click. This puts a full season in it: a roster,
games, leave, substitutes, a waitlist, and money owed in three different
directions.

**It goes through the HTTP API, not the database.** Ledger entries are
written by the route handlers — charging a season fee, refunding a
covered absence, correcting everyone when the air conditioning changes.
Inserting rows directly would mean reimplementing all of that here, and
the moment the copy drifted you would be developing against a set of
books the real code would never produce. Slower, and worth it.

It also doubles as a smoke test: if any of this fails, an endpoint the
app depends on is broken.

    VOLLEYFLOW_DEV_LOGIN=1 uv run uvicorn volleyflow.api.main:app --port 8000
    uv run python scripts/seed_dev.py

Then open http://localhost:5500/member.html?as=蘇懂

Refuses to run against anything but a local server, and deletes only the
club it created — see CLUB_NAME.
"""

from __future__ import annotations

import sys
from datetime import date, timedelta
from typing import Any
from urllib.parse import quote

import httpx

API = "http://localhost:8000"
CLUB_NAME = "測試球隊（seed）"

ORGANIZER = "蘇懂"
MEMBERS = [
    ("楊于嫺", "female"),
    ("莊", "male"),
    ("辭瑄", "female"),
    ("阿May", "female"),
    ("Ricky", "male"),
    ("Taco", "male"),
    ("WenChiao", "female"),
    ("Danny", "male"),
    ("Steven", "male"),
    ("Papeete", "female"),
    ("冠儀", "male"),
    ("官官", "female"),
    ("柏宣", "male"),
    ("延隆", "male"),
    ("昱承", "male"),
    ("亞彤", "female"),
    ("重均", "male"),
]

# The club's real numbers, so the ledger shows figures worth recognising:
# 13 games, 8 of them cooled, 52290 transferred, 540 a night for the air
# conditioning — which prices at 235 cooled and 205 plain, exactly.
TOTAL_VENUE_COST = "52290"
AC_SURCHARGE = "540"
COOLED_GAMES = 8
TOTAL_GAMES = 13


def token_for(name: str) -> dict[str, str]:
    """The dev-login header. See auth.verify_id_token — the name is
    percent-encoded because HTTP headers are ASCII."""
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

    # Start clean, but only ever remove this script's own club — anything
    # else in the dev database belongs to whoever put it there.
    for club in call("GET", "/clubs", ORGANIZER):
        if club["name"] == CLUB_NAME:
            print(f"removing the previous {CLUB_NAME} (id {club['id']})")
            call("DELETE", f"/clubs/{club['id']}", ORGANIZER)

    club = call("POST", "/clubs", ORGANIZER, {"name": CLUB_NAME})
    club_id = club["id"]
    print(f"created club {club_id}")

    # Half the season behind us and half ahead, so both a past game with
    # settled money and a future one you can still act on are visible.
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
            "location": "啪排郎",
            "change_deadline_days": 1,
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
        {"player_name": "楊于嫺", "game_id": games[7]["id"]},
    )
    call(
        "PUT",
        f"/absences/{covered['id']}/substitute",
        ORGANIZER,
        {"player_name": "小明", "gender": "male"},
    )
    print("楊于嫺 took leave, covered by 小明 — refunded")

    # Leave with nobody covering — not refunded, and a gap in the roster.
    call(
        "POST", "/absences", ORGANIZER, {"player_name": "莊", "game_id": games[8]["id"]}
    )
    print("莊 took leave, uncovered — not refunded")

    # Somebody in the club but not on this season's roster, bringing a
    # friend — the ordinary drop-in case, and the one that puts a
    # brought-by name on the money screen.
    #
    # Two things had to be learned here by running it. Signing in as a
    # name that is already on the roster creates a *second* player: a
    # roster entry is a name the organizer typed, with no identity behind
    # it, and the app has a whole feature for joining the two up. And
    # signing in isn't joining — an identity with no membership gets 403,
    # which is the right answer.
    visitor = "阿哲"
    sign_in(visitor)
    call("POST", f"/clubs/{club_id}/join", visitor)
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

    # The forecast was wrong for one night: turn it off and watch every
    # member's charge get corrected by an adjustment entry.
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
    settled_in_full = ["莊", "辭瑄", "阿May", "Ricky", "Taco"]
    part_paid = ["楊于嫺"]
    overpaid = ["冠儀"]

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
        ("楊于嫺", "owes money, took leave"),
        ("冠儀", "in credit"),
    ]:
        print(f"  http://localhost:5500/member.html?as={quote(name)}   ({note})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
