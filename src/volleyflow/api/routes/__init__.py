"""The API's routes, assembled from one module per resource.

This was a single 3,900-line `routes.py` until 2026-09-13. The split is
deliberately a move and nothing else: every function is byte-for-byte
where it was, and the test suite that passed before passes after. A
refactor that changes behaviour at the same time leaves you no way to
tell which half broke something.

The layering runs one way and is worth knowing before adding anything:

    clubs, seasons, games, attendance, players, money, reports
        |  (routes: request in, response out, one resource each)
        v
    _attendance     the rules about who is on court
        v
    _money          what that costs, and what it writes to the ledger
        v
    _people         who the caller is, and what they may do

`_people` imports nothing else in this package, and nothing imports a
route module. A helper that needs to reach back up a level is a helper
that belongs a level down.

Grouping by resource *does* change the order routes are registered in,
and FastAPI matches in registration order — so that had to be checked
rather than assumed. It is safe here because no path in this API can
swallow another: every pair was compared, by expanding `{param}` to
`[^/]+` and asking whether an earlier route with the same method matches
a later route's literal path. The answer was none, before the split and
after it. If a route is ever added that a parameterised one could match
— `/seasons/current` against `/seasons/{season_id}`, say — it has to be
registered first, and this file is where that ordering would live.
"""

from fastapi import APIRouter

from volleyflow.api.routes import (
    attendance,
    clubs,
    games,
    money,
    players,
    reports,
    seasons,
)

router = APIRouter()
for _module in (clubs, seasons, games, attendance, players, money, reports):
    router.include_router(_module.router)

__all__ = ["router"]
