# VolleyFlow

Signup, waitlist, and billing for a weekly volleyball game, run through
LINE. Members take leave or bring a friend from a LIFF page; the
organizer works the roster and the money from the same app.

The full write-up ??architecture and the reasoning behind each design
decision ??is still to be written. Until then,
[`CLAUDE.md`](CLAUDE.md) holds the scope and the working rules,
[`docs/billing-rules.md`](docs/billing-rules.md) is the authority on
anything involving money, and [`docs/dev-log.md`](docs/dev-log.md)
records how each stage actually got built.

## Running it locally

Two terminals, both from the repo root. One script each, because the
one-liner differs in every shell ??`VAR=1 cmd` and `&&` are bash, and
Windows PowerShell rejects both.

```powershell
.\scripts\dev-api.ps1     # the API on :8000, with local sign-in on
.\scripts\dev-web.ps1     # the pages on :5500
```

On bash or zsh, the same two things:

```bash
VOLLEYFLOW_DEV_LOGIN=1 uv run uvicorn volleyflow.api.main:app --reload --port 8000
cd frontend && python -m http.server 5500
```

Then open <http://localhost:5500/member.html?as=YourName>.

The interactive API docs are at <http://localhost:8000/docs> ??every
endpoint is listed there and can be called from the page.

A page takes its API from whatever host served it, so long as that host
is this machine or a private network address; anywhere else is
production (`apiBase()` in `shared.js`). Every page used to have the
production URL written into it, which meant local work silently edited
the real club's books ??there is a test that fails if one does it again.

### On a real phone

A desktop browser is not a phone. It can't show Safari's rendering, how
big a target is under a thumb, or what the keyboard covers ??three bugs
have reached a phone that every static check here passed. Both scripts
print a `192.168.x.x` address on startup; open that from a phone on the
same wifi and it talks to this machine:

```
http://192.168.1.101:5500/member.html?as=??
```

Windows asks once whether to let Python through the firewall ??allow it
for **private networks only**. Note what this combination means: while
those scripts are running, anyone on the same wifi can sign in as anyone
in the **dev** database. That is the reason it lives in a script and not
in any deployment.

`.env` points at the Neon **dev** branch. Production is only reachable
through Render's environment variables and the Neon console, and nothing
here should ever be pointed at it.

### Putting something in the database

The dev branch starts empty, so the app opens on "no club yet" with
nothing to click.

```
uv run python scripts/seed_dev.py
```

Builds a full season through the HTTP API ??a roster of 18, 13 games
priced at the club's real numbers, leave with and without a substitute,
a guest somebody brought, one night where the air conditioning forecast
was wrong, and members who have paid, part paid and overpaid. Re-running
it removes its own club first, so it always lands in the same state.

It goes through the API rather than inserting rows because ledger
entries are written by the route handlers: inserting directly would mean
reimplementing that here, and a copy that drifted would leave you
developing against books the real code would never produce. It is also a
smoke test ??if it fails, an endpoint the app depends on is broken.

### Being somebody, without LINE

Every page needs a verified identity, and a laptop has no LIFF to get
one from ??so without this, local pages stop at "open this in LINE" and
the only way to see a member's view is a phone against the live club.

`?as=<name>` signs you in as that person. The name is remembered for the
browser session, so links inside the app keep it; `?as=` with nothing
after it signs out.

```
http://localhost:5500/member.html?as=??        # a member's view
http://localhost:5500/organizer.html?as=??     # the same person, managing
http://localhost:5500/member.html?as=Ricky       # somebody else entirely
```

It is a hole in authentication, so it takes three things to open, and it
cannot be opened by accident:

| Lock | Where |
|---|---|
| `VOLLEYFLOW_DEV_LOGIN=1` must be set | the server's environment; production doesn't set it |
| the token must look like `dev:<name>` | `api/auth.py` ??nothing LINE issues looks like that |
| the page must be served from localhost | `shared.js`; the deployed site never offers it |

These identities are stored with a `dev:` prefix on `line_user_id`, so
they can never collide with a real LINE account or attach themselves to
a real person's ledger. See `tests/test_auth.py`.

## Checks

```
uv run ruff check .            # style
uv run ruff format .           # formatting
uv run mypy src scripts        # types
uv run pytest -q               # 313 tests
uv run lint-imports            # billing logic must not import the database
node --test tests/frontend/*.test.js   # 113 frontend tests
node tests/visual/check.js     # renders in a real browser and measures it
```

All except the last run in CI on every push. The visual check needs a
browser and is run by hand when layout changes ??see
[`tests/visual/README.md`](tests/visual/README.md).

## Deploying

Push to `main`. CI runs the checks, applies any database migrations to
production, then triggers the Render deploy ??in that order, because a
deploy that lands before its migration is what took production down
once. The frontend deploys to GitHub Pages separately when anything
under `frontend/` changes.

## Layout

```
src/volleyflow/
??? pricing.py       what one game costs one person, and the rounding rule
??? settlement.py    season-end reckoning: fees, refunds, drop-in charges
??? ledger.py        the append-only money history and its balance
??? schedule.py      Season and Game
??? players.py       Player and Membership
??? attendance.py    Absence, DropIn, WaitlistEntry
??? api/             FastAPI routes, request/response schemas, LINE auth
??? db/              SQLAlchemy models and the engine
??? notify/          LINE Messaging API client and the reminder job
```

The first six files are pure Python: no database, no web framework, no
I/O. That is deliberate and enforced ??`lint-imports` fails the build if
anything in there imports `volleyflow.db`. It is why money can be tested
without a database standing up, and why a change to the API can't
quietly change what someone is charged.
