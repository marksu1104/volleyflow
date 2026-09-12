# VolleyFlow

Signup, waitlist, and billing for a weekly volleyball game, run through
LINE. Members take leave or bring a friend from a LIFF page; the
organizer works the roster and the money from the same app.

## Why this exists

A real volleyball group in Taiwan runs its signups and payments over a
LINE group chat: who's coming this week, who's bringing a friend, who
still owes for last month. That works until it doesn't — a fixed member
who skips a night and the drop-in who covered their spot need to settle
up differently, air conditioning changes what one game costs partway
through a season, and "ask around in the chat" doesn't scale past about
fifteen people. VolleyFlow is that bookkeeping, done by a program instead
of by memory.

It's also a portfolio project: a way to build and defend, in an
interview, a small system with real money logic, a real multi-tenant
data model, and a real deployment pipeline — not a tutorial CRUD app.
[`CLAUDE.md`](CLAUDE.md) is the working agreement it was built under;
this file is the retrospective.

## What it does

**A member** opens a LIFF page inside LINE, already signed in. From
there: see the season's games and what each one costs, take leave (and
optionally name who's covering for them), bring a guest, join the
waitlist when a game is full, and see their own running balance.

**An organizer** manages the roster, marks payments received, corrects a
game's air conditioning setting after the fact, and settles a season.
Nothing this system sends goes to the group chat: the only push message
is a private one to the organizer, when a game is short-handed and
somebody has to go and ask. The club reads the roster in the app.

**Anyone** can create a club and become its organizer. A club is a full
tenant: its own roster, seasons, games, and books, invisible to every
other club sharing the same deployment.

## Architecture

```
  LINE app (phone)  --LIFF-->  Frontend            --HTTPS/JSON-->  API
                                plain HTML+JS                        FastAPI, Render
                                GitHub Pages                            |
                                                                         v
                                                                    Postgres (Neon)
                                                                         ^
                                                                         |
  Organizer, privately  <--push--  LINE Messaging API  <--reads---------+
    (one message: a short-handed game. Never the group chat.)

  GitHub Actions, on its own schedule:
    CI            test -> migrate production -> trigger the Render deploy
    reminders     daily, reads Postgres, pushes through the LINE client above
    keep-warm     pings /health every 10 min so the free instance doesn't sleep
```

Two static frontends (member + organizer share the same pages, gated by
role) talk to one FastAPI service over plain HTTPS + JSON. The service
is stateless — all state is in Postgres — so Render's free tier can let
it sleep between visits without losing anything; a `keep-warm` schedule
just makes that sleep less noticeable, and doesn't touch the database (a
`/health` route that never queries it).

## Design decisions

**Billing splits by capacity, not by headcount.** A member's season fee
is `total_venue_cost / (games × capacity)` — capacity, the number of
slots on court, not however many names happen to be on the roster this
week. The first version divided by the roster instead, and one person
leaving an 18-person season re-priced the other seventeen retroactively
— from $205 a night to $218 — while the drop-in standing in the empty
slot still paid $205, so the club collected the same gap twice. The
trade-off: an unfilled slot is money nobody pays. See
[`docs/billing-rules.md`](docs/billing-rules.md) for the full rule set,
including what a game's air conditioning does to that formula.

**The ledger is append-only.** Nothing is ever edited after the fact —
a corrected venue cost, a flipped air-conditioning setting, a member
added mid-season all write a new adjustment entry, never touch the old
one. That's what makes "why does this person's balance say $470" always
answerable by reading history forward, and it's the property a billing
system has to have before anything else about it matters.

**Billing logic can't import the database.** `pricing.py`, `settlement.py`,
and `ledger.py` are pure Python — no SQLAlchemy, no I/O — and
`import-linter` fails the build if that ever stops being true. Money math
gets tested without a database standing up, and an API-layer change can
never quietly change what somebody is charged, because the two are
structurally unable to reach into each other.

**A `Player` is one person for life, everywhere.** The same real human
joining two clubs is one `Player` row with one LINE identity, related to
each club through a separate `ClubMembership`. A drop-in this season who
becomes a fixed member next season carries their ledger history with
them, because it was always attached to the person, never to a role.

**Multi-tenancy came from watching a first version get in its own way.**
The original scope was one club, no tenant boundary — reversed once it
became clear the actual product shape ("someone signs in, starts a club,
becomes its organizer, then gathers members") is native to a
multi-tenant model. `Club` is the tenant boundary; everything else
(`Game`, `Absence`, `DropIn`, season membership) derives its club through
an existing foreign key rather than carrying a denormalized `club_id` of
its own.

**A join link carries a token, not a database id.** `/clubs/{id}/join`
still accepts a plain club id from an already-authenticated caller — a
deliberate, narrower scope than closing that off entirely would have
been (see `docs/backlog.md`) — but the *shared link* a visitor actually
clicks now carries an HMAC token instead of a sequential integer, so
finding a club by guessing small numbers no longer works from the one
surface a stranger would try it on.

**Settlement closes the books, and the screen stops offering.** Once a
season is settled every write against it is refused — attendance
included, which it hadn't been: you could sign somebody up on closed
books and charge them, or record a member's leave that earned a refund
nothing would ever pay, because a season cannot be settled twice. The
guard is deliberately separate from the change-deadline one, though
every caller wants both: that rule exempts the organizer and this one
must not. And the controls come off the screen rather than being left
there to be pressed and refused — every control on the game sheet is
reached through one of eight callbacks, so withholding those is the
whole of it in one place. The reading tabs stay; a settled season is
still worth looking at.

**Two tests earn back more time than they cost.** `tests/api/test_fuzz.py`
fires a few hundred randomly chosen operations at a season and checks
every invariant after each one — nobody on court twice, capacity never
exceeded, nobody owing for a night they didn't play — rather than
asserting one hand-written outcome. Run against a codebase already
covered by 370-odd specific tests, it found eight real bugs across two
sessions, most of them about money: a fixed member could be named as
someone else's substitute and billed twice; a roster could grow past its
own season's capacity, or start over capacity in the first place;
lowering capacity could re-price a season without correcting anyone's
ledger; a member's leave record outlived their removal from the roster
and kept mispricing games years later; cancelling a signup refunded
whatever the game costs *today* instead of what was actually charged,
leaving a residual balance whenever the air conditioning setting changed
in between. The general lesson: a rule only holds against the sequences
somebody thought to write down, until something else is doing the
choosing.

The second is `tests/visual/*.js` — Playwright driving a real browser.
Three bugs reached a phone that every static check here passed: `[hidden]`
losing to a class's own `display`, a tab handler that matched a data
attribute its own container carried (swallowing every other tap in the
game sheet), and a screen that flipped a saved change back for half a
second before flipping it forward again. That last one turned out to be
an ordering bug — a background refresh could overtake the write it was
meant to confirm — findable only by instrumenting a real page and
watching the timestamps, not by reading the code.

It also caught the one bug in this project that three sessions of reading
the code could not. A 500 on "add this person to the season", reported
three times and never reproduced: the roster screen reloads itself after
every change, redrawing its buttons, so a second tap lands while the
first request is still in flight, and both requests read "not a member
yet" before either writes. Writing that race down as a test — two
threads, real Postgres — turned up something worse than the crash and
completely silent: with a name the club had never seen, all four
concurrent requests *succeed*, each creating its own `Player` row, and
one person ends up on the roster four times with four season fees
charged. A read that decides something and a write that acts on it need
a lock between them, and `add_member` now takes one on the season row,
exactly as the signup path has always taken one on the game.

**CI migrates production before it deploys, and provably fails shut if
it can't.** Deploying code before its migration took production down
once. Now `alembic upgrade head` runs against production as its own CI
step, gated on a secret being present at all, before the Render deploy
hook fires — and it's a no-op on any push that changes no schema, so it
costs nothing on the pushes that don't need it.

**A crash reports itself without losing its own error page.** An
unhandled exception is caught by middleware, not a plain
`@app.exception_handler(Exception)` — a documented Starlette trap: it
attaches outside `CORSMiddleware`, so the response it builds never gets
an `Access-Control-Allow-Origin` header, and the browser reports a
same-origin violation instead of the real 500. That is exactly how a
crash in `link_player` first surfaced. The middleware logs every
unhandled exception, naming the deepest line of this project's own code
it passed through — `seasons.py:2173 in add_member` — because a database
error's own text is the failing SQL and identifies no route at all.

Locally only, the whole traceback comes back in the response body. That
is not a convenience: a browser-driven check can read a response and
cannot read the server's console, so a 500 that `smoke.js` had reported
three times as a bare status code handed over its cause in one run the
moment this existed.

It used to push a crash report to the organizer over LINE as well. That
was removed at their request — unasked-for messages that meant nothing to
the person receiving them, each one spending a push from the free tier's
200 a month. Worth recording as a judgement that was wrong: the feature
was built to solve a real problem (a 500 nobody could see) and solved it
by interrupting somebody who could not act on it. The log was the right
place the whole time.

## Checks

```
uv run ruff check .            # style
uv run ruff format .           # formatting
uv run mypy src scripts        # types
uv run pytest -q               # 417 tests, including a randomised sweep
uv run lint-imports             # billing logic must not import the database
node --test tests/frontend/*.test.js   # 175 frontend tests
node tests/visual/check.js     # renders in a real browser and measures it
node tests/visual/smoke.js     # presses every button and reports the dead ones
node tests/visual/feedback.js  # and how long each one takes to react
node tests/visual/chaos.js     # tapping faster than the network answers
node tests/visual/adverse.js   # the same, on a slow network and against refusals
```

The first five run in CI on every push. The last five need a browser and
are run by hand — `check.js` when layout changes, `smoke.js` and
`feedback.js` after anything that touches a click handler or a write,
`chaos.js` and `adverse.js` after anything that changes who may be on a
roster or moves money. `chaos.js` catches a change that saves, flips
back, and flips forward again — the failure mode of a screen whose write
skips the request queue, which is how it caught the money screen doing
exactly that a day after the roster screen was fixed. All five want the local servers up, and all but `check.js`
press destructive controls, so re-run `seed_dev.py` afterwards. See
[`tests/visual/README.md`](tests/visual/README.md).

`tests/api/test_fuzz.py` is worth knowing about on its own: rather than
asserting an outcome, it fires a few hundred randomly chosen operations
at a season and checks after every one that the rules still hold. It
found eight real bugs in code that 370 hand-written tests already
covered — see "Design decisions" above. `tests/api/invariants.py` holds
the rules it checks, reusable by any future test that wants the same
answers.

The SQLite test database runs with `PRAGMA foreign_keys=ON`. SQLite
parses `REFERENCES` and then ignores it unless every connection asks, so
without that line the whole suite was certifying rows that point at
nothing — and since `db/models.py` declares no `relationship()`, the ORM
sorts its mappers alphabetically and will happily insert `games` before
`seasons` if a route forgets to flush between them. A test fails the
moment that PRAGMA goes missing.

A handful of tests are marked `postgres` and hit the real Neon dev
branch instead of in-memory SQLite — excluded by default
(`uv run pytest -q`), run explicitly with `uv run pytest -m postgres`.
`scripts/backup_db.py` / `restore_db.py` are two of them: a plain-JSON
dump and restore, independent of Neon's own point-in-time recovery
(6 hours on the free plan) for a mistake found later than that.

## Running it locally

Two terminals, both from the repo root. One script each, because the
one-liner differs in every shell — `VAR=1 cmd` and `&&` are bash, and
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

The interactive API docs are at <http://localhost:8000/docs> — every
endpoint is listed there and can be called from the page.

A page takes its API from whatever host served it, so long as that host
is this machine or a private network address; anywhere else is
production (`apiBase()` in `shared.js`). Every page used to have the
production URL written into it, which meant local work silently edited
the real club's books — there is a test that fails if one does it again.

### On a real phone

A desktop browser is not a phone. It can't show Safari's rendering, how
big a target is under a thumb, or what the keyboard covers — three bugs
have reached a phone that every static check here passed. Both scripts
print a `192.168.x.x` address on startup; open that from a phone on the
same wifi and it talks to this machine:

```
http://192.168.1.101:5500/member.html?as=蘇懂
```

Windows asks once whether to let Python through the firewall — allow it
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

Builds a full season through the HTTP API — a roster of 18, 13 games
priced at the club's real numbers, leave with and without a substitute,
a guest somebody brought, one night where the air conditioning forecast
was wrong, and members who have paid, part paid and overpaid. Two of
those members are linked to a sign-in of their own, so the links it
prints at the end open on a real member's view rather than on "you
haven't joined a club". Re-running it removes its own club first, so it
always lands in the same state.

What it can't remove is the people. Deleting a club never deletes
players — a `Player` is global and outlives any one club — which is right
for the product and means twenty seed runs leave twenty casts behind.
After a heavy day of it:

```
uv run python scripts/tidy_dev_db.py          # count them
uv run python scripts/tidy_dev_db.py --yes    # delete them
```

It removes only a player who is in no club and that nothing at all points
at — no ledger entry, no signup, no absence, no queue place — and needs
`VOLLEYFLOW_DEV_LOGIN=1` before it will delete. (2,223 players, 2,162 of
them stranded, was the state that prompted it.)

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
| the page must be served from this machine or the local network | `shared.js`; the deployed site never offers it |

These identities are stored with a `dev:` prefix on `line_user_id`, so
they can never collide with a real LINE account or attach themselves to
a real person's ledger. See `tests/test_auth.py`.

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
├── api/             FastAPI: request/response schemas, LINE auth, the
│   │                invite-token module, crash reporting
│   └── routes/      one module per resource, over three helper layers
├── db/              SQLAlchemy models and the engine
└── notify/          LINE Messaging API client and the reminder job
```

`api/routes/` is the one place where layering is enforced by convention
rather than by a tool:

```
clubs  seasons  games  attendance  players  money  reports
    |  routes: request in, response out, one resource each
    v
_attendance   the rules about who is on court
    v
_money        what that costs, and what it writes to the ledger
    v
_people       who the caller is, and what they may do
```

It was one 3,884-line file until the layers were measured rather than
guessed at — a short `ast` pass asking which group each helper calls
into found exactly these three, with no cycles. The split moved code and
changed nothing else, which was checked by reading the old file's 41
route declarations out of git and comparing them against what the live
app registers: same routes, and no path can shadow another in either
order.

The first six files are pure Python: no database, no web framework, no
I/O. That is deliberate and enforced — `lint-imports` fails the build if
anything in there imports `volleyflow.db`. It is why money can be tested
without a database standing up, and why a change to the API can't
quietly change what someone is charged.

## What's left

[`docs/backlog.md`](docs/backlog.md) is the running list. What remains
are loose ends kept narrow on purpose rather than chased to 100%: the
join endpoint still accepts a plain club id from an authenticated
caller, the Chinese error-message table covers what an ordinary tap
reaches rather than all 41 routes, and the backup script has been
rehearsed against the dev branch but not production.
