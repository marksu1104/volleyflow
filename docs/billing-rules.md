# Billing Rules

Defines every rule the billing engine implements. The code translates this
document, not the other way around. To change a rule, change this file first,
then the code and tests.

Status: milestone 1 core, milestone 6 charge-timing update. Last updated 2026-09-10
(the cost is split by `capacity`, not by the current roster — see "Who the
cost is split between").

## Terms

Names here are the names used in the code. See `CLAUDE.md` §2.1 for the
full project vocabulary; this table covers only the billing-specific ones.

| Name | Meaning |
|---|---|
| `total_venue_cost` | Total court rental for the season, entered once when the season is created |
| `total_games` | Number of games generated when the season is created |
| `capacity` | How many play one game — and what the cost is divided between, see below |
| `share_per_game` | The atomic money unit, see below |
| `billable_games` | Games that still have to be paid for, see Cancellation |
| `surplus` | Money collected above the venue cost by rounding up, see Surplus |

## Core formula

```
ac_total   = ac_surcharge * (games with the air conditioning on)
base_each  = (total_venue_cost - ac_total) / total_games
share(g)   = ceil((base_each + (ac_surcharge if g is cooled else 0)) / capacity)
```

Rounded up to whole dollars, once per game's share, and nowhere else.
Every other amount is one of those shares:

```
member_season_fee = sum of share(g) over their billable games
drop_in_fee       = share(the game they signed up for)
absence_refund    = share(the game they missed)   # per covered absence
```

With `ac_surcharge` at zero — the default, and what every venue that
bundles air conditioning into its rate charges — every game's share is
identical and the whole thing collapses to the original single formula:

```
share_per_game = ceil(total_venue_cost / total_games / capacity)
```

### Who the cost is split between

`capacity`, not the number of fixed members. **Decided 2026-09-10**,
replacing a split by the current roster.

A share is what one *slot* costs for one night, and every person filling
a slot pays it — a fixed member through their season fee, a drop-in on
the night. The consequences are the point:

- **Adding or removing a member changes only that person's bill.**
  Under the old rule, one person leaving an 18-person season re-priced
  everybody: $205 a night became $218, retroactively, for seventeen
  people who had done nothing. Every roster edit rewrote the whole
  club's books.
- **A drop-in filling an empty slot costs the members nothing**, because
  they were never carrying that slot in the first place. Under the old
  rule the club collected the gap twice — once from the members whose
  share had gone up, and again from the drop-in standing in it.
- **The price is knowable when the season is booked** and never moves
  again unless the venue cost does.

The trade the club accepts for that: a slot nobody fills is money nobody
pays. Eighteen slots at $205 recover the venue cost exactly; sixteen
members and no drop-ins recover sixteen-eighteenths of it, and the rest
is the organizer's shortfall. Capacity is therefore a billing figure as
much as a roster limit, and worth setting to the number the club
actually expects on court.

### Air conditioning

Games do not all cost the same. A night with the air conditioning on
costs the club `ac_surcharge` more than one without, so splitting the
season total evenly across every game would make a drop-in on a cool
night subsidise the hot ones, and would refund an absence from an
expensive game at a cheap game's rate.

`ac_surcharge` is whatever the air conditioning costs **on the same
terms as `total_venue_cost`** — both are what the club actually pays, so
if the venue discounts the season the surcharge entered here is the
discounted one. The two are subtracted from each other, so mixing a list
price with a discounted total would misprice every game.

`ac_surcharge` is **per game, not per person**. The venue charges the
same for the air conditioning whether twelve people or eighteen turn up,
so a roster change has to move what each of them pays for it — which it
does, because the division by `capacity` happens after.

`total_venue_cost` stays authoritative: it is what the club actually
transfers, discounts included, so the air-conditioning portion is taken
*out* of it rather than added on top.

Worked example, from this club's own invoice:

```
13 games, 8 of them cooled, 18 members
list price 66595, special discount 14305, transferred 52290
air conditioning 540 a night, as billed after the discount

ac_total  = 540 x 8                  = 4320
base_each = (52290 - 4320) / 13      = 3690
cooled    = ceil((3690 + 540) / 18)  = 235
plain     = ceil(3690 / 18)          = 205
collected = (235 x 8 + 205 x 5) x 18 = 52290   exactly, no surplus
```

Where the 540 comes from, because it is not obvious and getting it
wrong misprices every game. The venue's list price for the air
conditioning is 180/hour over the 3.5 hours of court time, so 630 a
night; the discount brings it to 540, which happens to be the same
180/hour over 3. The discount is **not** applied evenly — 14.3% off the
air conditioning against 22.1% off the court, together making the 21.5%
off the total. So both "the air conditioning isn't discounted" and "540
is the discounted price" are true statements about different things, and
the figure this file wants is always the one that reconciles against
what was actually transferred:

```
3690 x 13 + 540 x 8 = 52290
```

The first implementation inferred 630 from the hourly rate over 3.5
hours and produced 237/202 — plausible, and wrong. The check that caught
it is the one above: the shares have to add back up to the transfer.

Whether a given night is cooled is a **forecast** when the season is
booked and a **fact** on the evening itself. It is therefore the one
season parameter expected to change mid-season: flipping it moves
`total_venue_cost` by `ac_surcharge` (the venue bills for the air
conditioning it ran) and corrects every member's charge with an
adjustment entry — never by editing what they were already charged. See
"Keeping the charge in sync when the inputs change" below.

A drop-in already charged for that game keeps the amount they were
charged. They pay the organizer in cash on the night, and chasing
someone for another $30 — or handing it back — because the forecast was
wrong costs more goodwill than the difference is worth. The members
absorb it, which is what a season fee is for.

### Why rounding happens exactly once

Rounding the charge and the refund independently opens a gap, because the two
roundings can point in different directions. Example with
`total_venue_cost=10000`, `total_games=7`, `capacity=5`:

| Approach | Pays | Max refund | Result |
|---|---|---|---|
| Round the season fee and the refund separately | 2000 | 2002 | A member absent the whole season nets a 2-dollar profit |
| Round `share_per_game` only | 2002 | 2002 | Symmetric |

Charges and refunds share one rounded unit, so a refund can never exceed what
the member paid. This is the engine's safety guarantee and needs a test.

### Precision

`Decimal` only, never `float`. `ROUND_CEILING` to whole dollars, applied once
when computing `share_per_game`. Nothing else rounds.

## Absences and refunds

Recording an absence does not by itself produce a refund. A refund happens
only when a drop-in actually covers the slot and pays for it.

The venue cost is fixed regardless of who shows up, so an absence alone
doesn't free up any money. When a drop-in covers the slot, their payment is
what gets passed on as the refund — the net effect is zero. When nobody
covers it, the gap is the absent member's own loss; the organizer does not
absorb it.

### FIFO when coverage falls short

When a game has more absences than drop-ins covering it, there aren't enough
refunds to go around. Order absences by the time they were recorded, earliest
first, and cover them until the drop-ins run out.

Three members record an absence (Alice 08/01, Bob 08/02, Carol 08/03) and two
drop-ins cover the game: Alice and Bob are refunded, Carol is not.

Ordering by timestamp needs no human judgment call, and if it's ever
disputed, the timestamp is the answer.

### Consequence

```
covered_absences(game) == drop_ins_filled(game)
```

Drop-in income always equals refunds paid out, so coverage has no net effect
on the books.

## Game cancellation

| Status | Venue cost | Billable |
|---|---|---|
| `SCHEDULED` | paid | yes |
| `CANCELLED_UNREFUNDED` | paid, not recoverable | yes |
| `CANCELLED_REFUNDED` | returned by the venue | no |

```
billable_games = total_games - count(CANCELLED_REFUNDED)
```

`share_per_game` divides by `total_games`, not `billable_games`, so cancelling
a game never changes it — only the multiplier changes. Since season fees are
now charged up front (see "Ledger" below), marking a game
`CANCELLED_REFUNDED` lowers every current member's `billable_games` by one
and writes a `+share_per_game` adjustment entry to each of their ledgers —
the organizer chooses this over `CANCELLED_UNREFUNDED` specifically to give
that credit back. Already-recorded absence/drop-in rows for that game are
otherwise untouched; the refund rule simply ignores `CANCELLED_REFUNDED`
games when totaling covered absences.

`CANCELLED_UNREFUNDED` needs no special-case code. It stays billable, nobody
attends, so no drop-in covers it, so by the refund rule nobody is refunded.
Everyone pays as normal, which is the intended outcome.

`CANCELLED_REFUNDED` games ignore any absence or signup records attached
to them.

### Venue refund amount

Defaults to `round(total_venue_cost / total_games)`, overridable by the
organizer, since in practice the venue decides how much comes back. It
affects `surplus` only, never what anyone owes.

## Surplus

Rounding up collects more than the venue actually costs. That difference
needs somewhere to live, otherwise the books do not balance.

```
surplus          = member_fees + drop_in_income - refunds - venue_cost_paid
venue_cost_paid  = total_venue_cost - sum(venue refunds)
```

With `total_venue_cost=10000`, `total_games=7`, `capacity=5`:

```
share_per_game     = ceil(285.714...) = 286
member_season_fee  = 286 * 7          = 2002
member_fees        = 2002 * 5         = 10010
surplus            = 10010 - 10000    = 10
```

What the surplus gets spent on (balls, incidentals) is the organizer's call.
The system only computes and tracks it.

## Invariants

Each of these needs a test.

```
I1  member_fees + drop_in_income - refunds == venue_cost_paid + surplus
I2  per member:  total_refunds <= season_fee_charged
I3  per game:    covered_absences == drop_ins_filled
I4  surplus >= 0
I5  every amount is a whole-dollar Decimal
```

## Ledger

Every player has a ledger: an append-only list of entries. The balance is
always the sum of the entries and is never stored as a value of its own.

Storing events instead of a running balance means the system can always
answer "why is this the number" by pointing at the entries. It's also what
makes cross-season balance carry-over work, and it doubles as the audit log.

Signs are from the player's point of view:

```
season fee charged            -2002
payment received              +2002
absence refund                 +286
drop-in fee charged            -286
carried in from last season       ±

balance > 0   the organizer owes the player
balance < 0   the player owes the organizer
```

At season end a balance is either settled in cash or carried into the next
season, which writes a carry-out entry here and a carry-in entry there that
sum to zero.

Entries are never modified, and each records who, when, and why.

### When the season fee is charged

The organizer's real collection flow (confirmed 2026-09-06) is: season fees
are collected **before the season starts**, drop-in fees are collected **on
the day, in person**. The engine matches that: a member's `season_fee_charged`
entry is written the moment they become a fixed member of a season with games
already scheduled — at season creation for the initial roster, at the moment
`POST /seasons/{id}/members` adds someone mid-season — not at season end.

Season end (`Settlement`) no longer charges the season fee; it only computes
and records each member's `absence_refund` for the season just finished, then
locks the season. This matches the organizer's real mental model: by the time
a season ends, everyone's season fee is already settled one way or another —
the only open question left is who gets refunded for a covered absence.

### Keeping the charge in sync when the inputs change

`share_per_game` depends on `total_venue_cost`, `total_games`, and
`capacity` — any of which can change after members have already been
charged: the organizer edits the venue cost, raises or lowers the
capacity, or cancels a game with a refund (which lowers
`billable_games`). Adding or removing a **member** no longer belongs on
that list, which is the point of splitting by capacity: it changes that
person's own charge and nobody else's. Each of these
recomputes every current member's correct `season_fee_charged` total and
writes **one adjustment entry** per member for the difference between that
target and what's already on their ledger for this season — never edits or
deletes the original entry. This keeps the append-only guarantee (CLAUDE.md
2.5) intact: the full history of "what this person was charged, and why it
changed" stays on the ledger, not just the final number.

A member removed from the season gets their season-fee charge reversed to
zero the same way — one adjustment entry equal to the negative of whatever
they'd already been charged for this season. Their past absence/drop-in
ledger entries (if any) are untouched; only the season-fee portion reverses.

```
target_charge(player)   = -share_per_game * billable_games   # via settle_member
already_charged(player) = sum of this season's season_fee_charged entries
adjustment               = target_charge - already_charged
```

No adjustment entry is written when `adjustment == 0` — an edit that doesn't
change anyone's math (e.g. changing the venue location) writes nothing.

## Open questions

- Drop-in cancellation deadline, currently unlimited. Does cancelling late
  still incur the fee?
- Is there an absence deadline? Does a late absence forfeit the refund?
