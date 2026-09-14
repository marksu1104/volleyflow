"""Nobody outside a club can read or change anything in it.

A club is the tenant boundary, so this is the property the
whole multi-club design rests on. Most tests check one route at a time;
this one sends every route in the API, aimed at one club's data, as three
different kinds of outsider — and checks two things for each:

  * the request is refused (401, 403 or 404), and the refusal carries none
    of the club's own words back;
  * afterwards the club is exactly as it was. A request that answers "no"
    but changed something on the way is the failure worth catching, and
    only comparing the whole club before and after can see it.

Written 2026-09-15, before the app was opened to clubs other than the
developer's own.
"""

from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.api.factories import auth_headers, create_club, identify, start_season

# Words that exist only inside club A. None may appear in anything an
# outsider is sent back.
SECRETS = ["甲隊祕密", "祕密會員", "祕密臨打", "祕密候補"]
FUTURE = "2031-01-07"
REFUSED = (401, 403, 404)


def _club_a(client: TestClient) -> dict[str, Any]:
    """A club with one of everything in it: members, a game, an absence, a
    confirmed drop-in, a queue, and the season fees on the ledger."""
    club = create_club(client, name="甲隊祕密")
    season = start_season(
        client,
        club_id=club["id"],
        organizer_token=club["organizer_token"],
        member_names=["祕密會員", "另一位"],
        capacity=3,
        game_dates=[FUTURE],
    )
    game = season["games"][0]["id"]
    member = next(
        m
        for m in client.get(f"/seasons/{season['id']}").json()["members"]
        if m["name"] == "祕密會員"
    )
    absence = client.post(
        "/absences", json={"player_name": "祕密會員", "game_id": game}
    ).json()
    drop_in = client.post(
        "/drop-ins", json={"player_name": "祕密臨打", "game_id": game}
    ).json()
    client.post("/drop-ins", json={"player_name": "祕密臨打二", "game_id": game})
    queued = client.post(
        "/drop-ins", json={"player_name": "祕密候補", "game_id": game}
    ).json()
    assert drop_in["status"] == "confirmed", drop_in
    assert queued["status"] == "waitlisted", queued
    return {
        "club": club["id"],
        "season": season["id"],
        "game": game,
        "member": member["id"],
        "absence": absence["id"],
        "drop_in": drop_in["id"],
        "entry": queued["id"],
        "organizer": club["organizer_token"],
    }


def _attempts(a: dict[str, Any], stranger_id: int) -> list[tuple[str, str, Any]]:
    """Every route that can name club A's data, with a body that would be
    accepted from somebody entitled to send it — so a refusal is about who
    is asking, never about a malformed request.

    Joining is last on purpose: if it were let through, everything after it
    would be asked as a member, and the results would say nothing about
    outsiders.
    """
    c, s, g, m = a["club"], a["season"], a["game"], a["member"]
    return [
        ("GET", f"/clubs/{c}", None),
        ("GET", f"/clubs/{c}/invite", None),
        ("GET", f"/clubs/{c}/my-guests", None),
        ("GET", f"/clubs/{c}/members", None),
        ("GET", f"/clubs/{c}/seasons", None),
        ("GET", f"/clubs/{c}/balances?season_id={s}", None),
        ("GET", f"/clubs/{c}/players/{m}/ledger", None),
        ("GET", f"/players/{m}/clubs", None),
        ("GET", f"/seasons/{s}", None),
        ("GET", f"/seasons/{s}/join-pool", None),
        ("GET", f"/seasons/{s}/settlement", None),
        ("POST", "/absences", {"player_name": "另一位", "game_id": g}),
        ("POST", f"/absences/{a['absence']}/cancel", {}),
        (
            "PUT",
            f"/absences/{a['absence']}/substitute",
            {"player_name": "外人找的人", "gender": "male"},
        ),
        ("POST", "/drop-ins", {"player_name": "外人自己", "game_id": g}),
        (
            "POST",
            f"/games/{g}/drop-ins",
            {"people": [{"player_name": "外人朋友", "gender": "male"}]},
        ),
        ("POST", f"/waitlist/{a['entry']}/cancel", {}),
        ("POST", f"/waitlist/{a['entry']}/promote", {}),
        ("POST", f"/drop-ins/{a['drop_in']}/cancel", {}),
        ("POST", f"/clubs/{c}/players/{m}/link", {"line_player_id": stranger_id}),
        ("PUT", f"/clubs/{c}/members/me/intent", {"wants_fixed_membership": True}),
        ("DELETE", f"/clubs/{c}/members/{m}", None),
        ("POST", f"/games/{g}/cancel", {"refunded": True}),
        ("PUT", f"/games/{g}/air-conditioning", {"air_conditioned": True}),
        (
            "POST",
            f"/clubs/{c}/players/{m}/payments",
            {"amount": "100", "season_id": s},
        ),
        ("PUT", f"/players/{m}/gender", {"gender": "male"}),
        ("PUT", f"/players/{m}/name", {"name": "被外人改名"}),
        ("PATCH", f"/seasons/{s}", {"capacity": 9}),
        ("POST", f"/seasons/{s}/members", {"player_name": "外人"}),
        ("DELETE", f"/seasons/{s}/members/{m}", None),
        ("POST", f"/seasons/{s}/settle", {}),
        (
            "POST",
            f"/clubs/{c}/seasons",
            {
                "total_venue_cost": "1000",
                "game_dates": [FUTURE],
                "member_names": ["外人"],
                "capacity": 5,
            },
        ),
        ("DELETE", f"/seasons/{s}", None),
        ("DELETE", f"/clubs/{c}", None),
        # A forged token: an outsider has no link to give.
        ("POST", f"/clubs/{c}/join", {"invite": f"{c}.00000000000000000000"}),
    ]


def _snapshot(client: TestClient, a: dict[str, Any]) -> Any:
    """Everything about club A its own organizer can see."""
    h = auth_headers(a["organizer"])
    return (
        client.get(f"/seasons/{a['season']}", headers=h).json(),
        client.get(
            f"/clubs/{a['club']}/balances?season_id={a['season']}", headers=h
        ).json(),
        client.get(f"/clubs/{a['club']}/members", headers=h).json(),
        client.get(f"/clubs/{a['club']}/seasons", headers=h).json(),
    )


def _let_through(
    client: TestClient, a: dict[str, Any], headers: dict[str, str], stranger_id: int
) -> list[str]:
    """Every request that got past, and every one that changed club A —
    whatever it answered. A snapshot after each request is what pins a
    change on the request that made it, rather than only reporting at the
    end that something, somewhere, did."""
    problems = []
    before = _snapshot(client, a)
    for method, path, body in _attempts(a, stranger_id):
        r = client.request(method, path, json=body, headers=headers)
        leaked = [word for word in SECRETS if word in r.text]
        after = _snapshot(client, a)
        changed = after != before
        if r.status_code not in REFUSED or leaked or changed:
            problems.append(
                f"{method} {path} -> {r.status_code}"
                + (f" LEAKS {leaked}" if leaked else "")
                + (" CHANGED CLUB A" if changed else "")
                + f"  {r.text[:90]}"
            )
        before = after
    return problems


def test_another_clubs_organizer_can_neither_read_nor_change_it(
    client: TestClient,
) -> None:
    # The strongest outsider there is: signed in, and an organizer — just
    # of a different club.
    a = _club_a(client)
    stranger = identify(client, "外人")
    client.post(
        "/clubs", json={"name": "乙隊"}, headers=auth_headers(stranger["token"])
    )
    before = _snapshot(client, a)

    problems = _let_through(client, a, auth_headers(stranger["token"]), stranger["id"])

    assert not problems, "let through:\n  " + "\n  ".join(problems)
    assert _snapshot(client, a) == before, "club A was changed by somebody outside it"


def test_nobody_signed_in_can_touch_it_at_all(client: TestClient) -> None:
    a = _club_a(client)
    before = _snapshot(client, a)

    problems = _let_through(client, a, {"Authorization": ""}, stranger_id=999_999)

    assert not problems, "let through:\n  " + "\n  ".join(problems)
    assert _snapshot(client, a) == before


def test_the_developer_gets_no_way_into_a_club(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Reading every club's problem reports is the developer's one extra
    # power, and it must not quietly extend to the clubs themselves.
    a = _club_a(client)
    dev = identify(client, "Developer")
    monkeypatch.setenv("DEVELOPER_LINE_USER_ID", dev["token"])
    before = _snapshot(client, a)

    problems = _let_through(client, a, auth_headers(dev["token"]), dev["id"])

    assert not problems, "let through:\n  " + "\n  ".join(problems)
    assert _snapshot(client, a) == before
