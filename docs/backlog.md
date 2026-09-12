# What is left

Written 2026-09-11, most of it closed out on 2026-09-12. `CLAUDE.md`
still holds the scope and the rules; this is the running list of what
has not been built yet, in the order it should be built.

Anything not on this list is either done or deliberately out of scope
(see CLAUDE.md §4, "Explicitly out of scope").

## 1. Notifications: one message, to the organizer only

`reminders.yml` pointed at the Neon **dev** branch since the day it was
written — the same secret `ci.yml`'s Postgres tests use — so its daily
09:00 run "succeeded" against seed data and no reminder had ever reached
a real game. Fixed on 2026-09-12: it now reads `PRODUCTION_DATABASE_URL`.

The group message is gone, by the organizer's decision (2026-09-12): the
club already reads the roster in the app, and a bot posting into the
chat every week is noise they did not ask for. `push_to_group` and
`LINE_GROUP_ID` were deleted rather than left behind a flag, so nothing
can quietly start posting again. What remains is the short-roster alert
to the organizer alone (`LINE_ORGANIZER_USER_ID`, already set), which
will fire for real on the next short-handed game.

The crash report over LINE is gone too (2026-09-12), for the same
reasons and at the same person's request: unprompted messages that meant
nothing to whoever read them, each one spending a push from the same
200 a month the short-roster alert needs. An unhandled exception now
leaves a log line on Render naming the route and the line that raised
it, which is where a crash report belongs.

**What this leaves is one push message in the whole system**, and that
is the right default to hold: `notify/reminders.py` sends the
short-roster alert and nothing else. The five message types described in
a previous draft of this file (new joiners, unpaid-fee chase,
settlement, waitlist promotion, and the pre-game reminder) are a
wishlist and, after two rounds of this, mostly an unwanted one. Adding
any of them is real, separate work and needs asking first.

## 2. `routes.py` split

3,600-odd lines and over 40 routes now — larger than a week ago, since
this stage's fixes (capacity limits, the invite token, the crash-
reporting middleware) all landed in it. The seams are clear enough
(clubs, seasons, attendance, money), but splitting it is a large diff
with no behaviour change, and doing it in the same wave as several real
behaviour changes to the same file is exactly the "busy moment" this
note has always warned against. Wants its own quiet stage.

## 3. Loose ends, deliberately left open

- **Joining a club without the invite link.** `?invite=<token>` (an HMAC
  of the club id, `src/volleyflow/api/invites.py`) closed the
  reconnaissance half of "the invite link carries a guessable id" — a
  stranger can no longer find a club's name by trying small integers in
  the shared link. `POST /clubs/{id}/join` itself still accepts a plain
  id from any already-identified caller, so a determined stranger who
  calls it directly, bypassing the link, still can. Closing that fully
  means making the token *required* on join, which touches roughly sixty
  existing test call sites that use the endpoint as ordinary setup — a
  large, low-value diff for a personal club with no history of abuse.
  Left open on purpose, not missed.
- **The error-message catalogue covers what an ordinary tap reaches, not
  every route.** `translateApiError` in `shared.js` (2026-09-12) is a
  curated table of fixed phrases, not a code every one of forty routes
  sends — the same "sixty call sites" trade-off as above, applied to a
  different endpoint. An error nobody has actually hit yet still shows
  its English reason, wrapped in a Chinese sentence rather than replacing
  it.
- **No restore drill against production.** The drill itself now exists
  and has been run for real — `scripts/backup_db.py` / `restore_db.py`,
  exercised against the dev branch on 2026-09-12, including the actual
  disaster-then-restore sequence, not just a read of the code. What
  remains is doing it once against production, which needs the
  organizer's own access — this project's rule is that production
  credentials are never handled from here. Neon's own point-in-time
  restore is the faster first line of defence regardless, but only
  reaches 6 hours back on the free plan (1GB of changes) — not long
  enough for a mistake noticed the next day, which is what the script is
  for.

## Not doing

- **Immediate absence refunds.** Refunds land at settlement
  (`CLAUDE.md` 2.4). Showing a member their *expected* refund mid-season
  would be reasonable and is not built; changing when the money actually
  moves is not planned.
- **Merging duplicate people.** The pickers stop new duplicates being
  created and pull repeat signups onto one person over time. A tool to
  merge two existing rows would have to move ledger entries between
  them, and that is a lot of risk for a problem that is now shrinking on
  its own.
