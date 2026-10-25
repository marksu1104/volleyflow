"""The API's routes, one module per resource.

The layering runs one way:

    clubs, club_joining, club_members, seasons, season_roster,
    season_settlement, games, attendance, players, money, reports, developer
        |  (routes: request in, response out)
        v
    _attendance     the rules about who is on court
        v
    _money          what that costs, and the ledger entries it writes
        v
    _people         who the caller is, and what they may do

`_people` imports nothing else in this package, and nothing imports a
route module.

FastAPI matches routes in registration order. No path here can match
another's (tests check this), so the order below does not matter
today; a route like `/seasons/current` beside `/seasons/{season_id}`
would have to be registered first.
"""

from fastapi import APIRouter

from volleyflow.api.routes import (
    attendance,
    club_joining,
    club_members,
    clubs,
    developer,
    games,
    money,
    players,
    reports,
    season_roster,
    season_settlement,
    seasons,
)

router = APIRouter()
for _module in (
    clubs,
    club_joining,
    club_members,
    seasons,
    season_roster,
    season_settlement,
    games,
    attendance,
    players,
    money,
    reports,
    developer,
):
    router.include_router(_module.router)

__all__ = ["router"]
