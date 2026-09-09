# VolleyFlow

Signup, waitlist, and billing for a weekly volleyball game, run through
LINE. Members take leave or bring a friend from a LIFF page; the
organizer works the roster and the money from the same app.

The full write-up — architecture and the reasoning behind each design
decision — is still to be written. Until then,
[`CLAUDE.md`](CLAUDE.md) holds the scope and the working rules,
[`docs/billing-rules.md`](docs/billing-rules.md) is the authority on
anything involving money, and [`docs/dev-log.md`](docs/dev-log.md)
records how each stage actually got built.

## Running it locally

Two terminals. Both from the repo root.

```bash
# The API. --reload restarts it whenever a file is saved.
# VOLLEYFLOW_DEV_LOGIN turns on the local identity described below.
VOLLEYFLOW_DEV_LOGIN=1 uv run uvicorn volleyflow.api.main:app --reload --port 8000

# The pages. Anything that serves static files will do.
cd frontend && python -m http.server 5500
```

Then open <http://localhost:5500/member.html?as=YourName>.

The interactive API docs are at <http://localhost:8000/docs> — every
endpoint is listed there and can be called from the page.

`.env` points at the Neon **dev** branch. Production is only reachable
through Render's environment variables and the Neon console, and nothing
here should ever be pointed at it.

### Putting something in the database

The dev branch starts empty, so the app opens on "no club yet" with
nothing to click.

```bash
uv run python scripts/seed_dev.py
```

Builds a full season through the HTTP API — a roster of 18, 13 games
priced at the club's real numbers, leave with and without a substitute,
a guest somebody brought, one night where the air conditioning forecast
was wrong, and members who have paid, part paid and overpaid. Re-running
it removes its own club first, so it always lands in the same state.

It goes through the API rather than inserting rows because ledger
entries are written by the route handlers: inserting directly would mean
reimplementing that here, and a copy that drifted would leave you
developing against books the real code would never produce. It is also a
smoke test — if it fails, an endpoint the app depends on is broken.

### Being somebody, without LINE

Every page needs a verified identity, and a laptop has no LIFF to get
one from — so without this, local pages stop at "open this in LINE" and
the only way to see a member's view is a phone against the live club.

`?as=<name>` signs you in as that person. The name is remembered for the
browser session, so links inside the app keep it; `?as=` with nothing
after it signs out.

```
http://localhost:5500/member.html?as=蘇懂        # a member's view
http://localhost:5500/organizer.html?as=蘇懂     # the same person, managing
http://localhost:5500/member.html?as=Ricky       # somebody else entirely
```

It is a hole in authentication, so it takes three things to open, and it
cannot be opened by accident:

| Lock | Where |
|---|---|
| `VOLLEYFLOW_DEV_LOGIN=1` must be set | the server's environment; production doesn't set it |
| the token must look like `dev:<name>` | `api/auth.py` — nothing LINE issues looks like that |
| the page must be served from localhost | `shared.js`; the deployed site never offers it |

These identities are stored with a `dev:` prefix on `line_user_id`, so
they can never collide with a real LINE account or attach themselves to
a real person's ledger. See `tests/test_auth.py`.

## Checks

```bash
uv run ruff check .            # style
uv run ruff format .           # formatting
uv run mypy src scripts        # types
uv run pytest -q               # 292 tests
uv run lint-imports            # billing logic must not import the database
node --test tests/frontend/*.test.js   # 106 frontend tests
node tests/visual/check.js     # renders in a real browser and measures it
```

All except the last run in CI on every push. The visual check needs a
browser and is run by hand when layout changes — see
[`tests/visual/README.md`](tests/visual/README.md).

## Deploying

Push to `main`. CI runs the checks, applies any database migrations to
production, then triggers the Render deploy — in that order, because a
deploy that lands before its migration is what took production down
once. The frontend deploys to GitHub Pages separately when anything
under `frontend/` changes.

## Layout

```
src/volleyflow/
├── pricing.py       what one game costs one person, and the rounding rule
├── settlement.py    season-end reckoning: fees, refunds, drop-in charges
├── ledger.py        the append-only money history and its balance
├── schedule.py      Season and Game
├── players.py       Player and Membership
├── attendance.py    Absence, DropIn, WaitlistEntry
├── api/             FastAPI routes, request/response schemas, LINE auth
├── db/              SQLAlchemy models and the engine
└── notify/          LINE Messaging API client and the reminder job
```

The first six files are pure Python: no database, no web framework, no
I/O. That is deliberate and enforced — `lint-imports` fails the build if
anything in there imports `volleyflow.db`. It is why money can be tested
without a database standing up, and why a change to the API can't
quietly change what someone is charged.
