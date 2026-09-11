# What is left

Written 2026-09-11, after a week of using the app for real turned up
far more than the milestone plan anticipated. `CLAUDE.md` still holds
the scope and the rules; this is only the running list of what has not
been built yet, in the order it should be built.

Anything not on this list is either done or deliberately out of scope
(see CLAUDE.md §4, "Explicitly out of scope").

## 1. Roster changes, before and after a season starts

The last piece that is about **data correctness** rather than looks, so
it goes first.

Part of this landed on 2026-09-12, because a random sweep
(`tests/api/test_fuzz.py`) proved it was a live bug rather than a missing
nicety: adding a member now refuses when the roster is already the size
of the capacity, or when any game has no free slot. What is still missing
is everything around that refusal — the confirmations below, and the
"raise the capacity" route out of it that the message tells people to
take. Creating a season with more members than slots is also still
allowed, which is now the only way left to build an over-capacity
roster.

The rules, as specified on 2026-09-10:

| When | Adding | Removing |
|---|---|---|
| Before the first game | free, no confirmation — but never past `capacity` | free, no confirmation |
| After the season has started, roster full | refused, with the reason and a way to raise the capacity | allowed, with a confirmation |
| After the season has started, roster short | allowed, with a confirmation | allowed, with a confirmation |

"Started" means the first game's date has passed. The confirmations
exist because a mid-season roster change moves money — the person
joining or leaving is charged or refunded for the whole season, and
under the capacity-based split (see `docs/billing-rules.md`) it is only
ever *their* bill that moves, which is worth saying on the screen.

Not built at all yet: today the roster is editable without limit and
without prompts.

## 2. The management screens

Three things reported as looking wrong, none of them yet addressed.

**The 新增臨打 block on the overview.** Called out as ugly and
inconsistent with the member page's signup sheet, which has since been
rebuilt around a shared picker (`renderPersonPicker`). This block should
use the same picker rather than a bare name field and a select.

**The roster page order.** `organizer-members.html` currently reads:
join link, people not on this season, add a guest, then the actual
roster last. It should lead with **本季固定成員**, then a section of its
own for people **waiting to be approved** (`wants_fixed_membership ===
true`, currently only a tag inside another list), then everyone else,
collapsed.

**The 帳務 screen** has not been looked at since the wording pass. Worth
a read-through with the same eye once the two above are done.

## 3. Notifications

Nothing is built beyond `notify/reminders.py`, and `LINE_GROUP_ID` has
never been set, so **not one message has ever been sent**.

The five types wanted, and the quota they cost (200 push messages a
month, free tier — reply messages are unlimited but only answer
somebody):

| Message | Roughly |
|---|---|
| Two days before a game | 4/month |
| The day before, with the price and whether the air conditioning is on | 4/month |
| New joiners, batched — at most one a day | ≤5/month |
| Unpaid-fee chase, at most four a season | 4/season |
| Season settlement | 1/season |
| Somebody promoted off the waitlist, to that person | a handful |

About 21 a month against 200, so quota is not the constraint; deciding
what is worth interrupting people for is. Two earlier candidates —
「被指定為代打」and「場次取消」— were dropped for that reason.

## 4. Loose ends

- **`routes.py` is 2,900 lines and 40 routes.** It has not been split.
  The seams are clear enough (clubs, seasons, attendance, money), but
  splitting it is a large diff with no behaviour change, so it wants a
  quiet moment rather than a busy one.
- **The invite link carries a guessable club id** (`?club=12`). A token
  would be better.
- **The API's error messages are English, and the UI shows them raw.**
  Every `raise HTTPException` detail goes straight into a Chinese toast:
  「加入失敗：Already a member of this season」. CLAUDE.md asks for the
  user-facing text to be Traditional Chinese and centralized in one
  module, so this wants a message catalogue keyed by code rather than
  translating strings where they are raised. Most visible on the roster
  screen, where the new capacity refusals live.
- **No error monitoring.** A 500 in production is invisible unless
  somebody reports it. One existed for the whole life of the project and
  was only found on 2026-09-12: linking a LINE account to a roster entry
  crashed on a foreign key whenever that account had ever signed a guest
  up, and the browser reported it as a CORS error, because a crash
  carries no headers.
- **No restore drill.** Neon keeps backups; nobody has ever tried
  restoring one.
- **The README's full write-up** — architecture and the reasoning behind
  each decision — is still the placeholder paragraph. It is the
  resume-facing artifact, and milestone 5 says it gets written once, at
  the end.

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
