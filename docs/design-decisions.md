# Design decisions

The reasoning behind VolleyFlow's main choices, and the incidents that
shaped several of them. The README has the short version; the billing
rules themselves are in [billing-rules.md](billing-rules.md).

## Architecture notes

Two static frontends (member + organizer share the same pages, gated by
role) talk to one FastAPI service over plain HTTPS + JSON. The service
is stateless — all state is in Postgres — so Render's free tier can let
it sleep between visits without losing anything; a `keep-warm` schedule
just makes that sleep less noticeable, and doesn't touch the database (a
`/health` route that never queries it).

That last detail has a consequence worth knowing. Render stays awake,
but Neon's free tier suspends its compute after a few idle minutes and
drops every open connection — so the pool used to hand the next request a
dead one, and the first tap of the evening was a 500. Measured, not
assumed: seven minutes idle, and the app's engine failed with
`AdminShutdown` while the same engine with `pool_pre_ping` recovered in
1.7 seconds. The engine now pings a pooled connection before using it.

## Decisions

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

**Joining a club takes its invite link, not its id.** The shared link
carries an HMAC token instead of a sequential integer, and
`POST /clubs/{id}/join` refuses without the token that matches that id.
For a while only the first half was true: the link hid the id, but the
endpoint still took a bare one from anybody signed in — so trying small
integers joined every club in turn, and a member can read the whole
roster. It was left open deliberately while the app ran one club, and
closed before it was opened to others, when a test that sends every
route in the API at one club as an outsider (`test_tenant_isolation.py`)
was let straight in. The same test found `GET /clubs/{id}` handing any
signed-in stranger a club's name, and a refused signup writing a person
into the club before refusing — both closed the same day.

An HMAC is only as secret as its key, and for a while production's key
was the development fallback written in this public repository — the code
fell back to it whenever `INVITE_TOKEN_SECRET` wasn't set, on the stated
assumption that production set it. Found by signing a token with the
public key and sending it to the live API, which accepted it. The
fallback is now allowed under local sign-in only; anywhere else a missing
key is a 503 and a visibly broken invite screen, which is the right way
for a security control to fail.

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

**A season asks only for what is due by it.** A member's fee is charged
the moment they join a season, so a club that books January in October
already has January's fee on everybody's ledger. Summing the whole
ledger put that fee into October's settlement as money owed, and the
button that squares it would have booked January's money as October's.
Every figure anybody is asked to act on — the fee screen, settlement, the
fee notice, a member's own balance — now counts that season and earlier
ones, never a later one. It also fixed the opposite case: a refund kept
for next season used to sit on the books untouched while the fee screen
collected the plain fee. Found by walking a two-season example through a
clickable mock-up with a ledger beside it and requiring every balance to
reach zero; that check is now `tests/api/test_two_season_flow.py`.

**One column, one meaning.** Who signed a guest up was also used to mean
who pays for them, and naming a queued guest as somebody's substitute
overwrote it with the absent member — so when the substitute went back
to the queue, the roster named a person who had never signed them up.
The first fix added a column to remember the original; the better one
removed that column again and stopped the overwrite. The guest's fee is
on the guest's own ledger, the person who signed them up is whom the
organizer asks, and that never changes.

**Playing and waiting are one table.** The deeper cause under that bug:
the court and the queue were two tables, and moving somebody between
them deleted a row in one and copied its fields into a new row in the
other — along five separate paths. Two real bugs were a field one path
forgot: a queued person vanishing outright, and the wrong bringer. Now a
signup has a `status`, and a move changes that one column; there is
nothing left to forget to carry over. The risk runs the other way —
any query that counts who is on court must leave the queue out — so
every such query goes through one shared condition, and the conversion
into the billing engine refuses a queued row outright, which turns a
forgotten filter into a loud failure instead of a refunded absence
nobody filled. The data migration refuses to guess if anybody is on
court and queued at once, was rehearsed up and back down on a copy of
the database with every field compared, and the restore script reads
backups from before the change into the new shape.

**A message that can't be taken back is claimed before it is sent.** The
deadline notices run from a job every ten minutes, and the fee notice
from a button; either can run twice. Each game, and each member's fee
notice, is marked and committed first and pushed second, so an
overlapping run or a double tap finds nothing left to send. The cost is
the other failure — a crash between the two leaves somebody untold —
which the organizer can see and fix, where a duplicate charge reminder
can only be apologised for.

**Notices are tested for how they read on a phone.** A LINE bubble on a
small phone fits about ten Chinese characters a line, measured by
simulating four phone widths and calibrating to the one where the first
version wrapped. A test sets every line of every notice against that
budget; it caught two over-long lines in the redesign that introduced it.

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
it passed through — `season_roster.py:120 in add_member` — because a database
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

Problem reports followed three days later, for a sharper reason: each one
was a push — two with a screenshot — out of the 200 a month the
short-roster alert depends on, and reports arrive in bursts exactly when
something is broken. They are stored now and read on a developer-only
page, `reports.html`, gated by `DEVELOPER_LINE_USER_ID` (unset means
nobody can read them, never everybody). Screenshots stopped being public
at the same time: they sat at an open URL only so LINE's servers could
fetch them, and a screenshot can show a name and a balance.

## Testing notes

`firstrun.js` is the odd one out: it wants an **empty** database, because
it checks the one screen every user sees exactly once. Two management
pages were confidently telling a brand-new user that their (nonexistent)
club had not started a season yet, and offering to copy its invite link.
Every other check runs against seed data, so none of them could see it.

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

## Code layout notes

`api/routes/` is the one place where layering is enforced by convention
rather than by a tool:

```
routes (one module per resource): request in, response out
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
order. The season and club routes were later split the same way, by
what they act on, with a route-by-route comparison before and after.

The domain modules (`pricing.py`, `settlement.py`, `ledger.py`,
`schedule.py`, `players.py`, `attendance.py`) are pure Python: no
database, no web framework, no I/O. That is deliberate and enforced —
`lint-imports` fails the build if anything in there imports
`volleyflow.db`. It is why money can be tested without a database
standing up, and why a change to the API can't quietly change what
someone is charged.
