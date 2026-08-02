# Billing Rules

Defines every rule the billing engine implements. The code translates this
document, not the other way around. To change a rule, change this file first,
then the code and tests.

Status: milestone 1. Last updated 2026-07-31.

## Terms

Names here are the names used in the code. See `CLAUDE.md` §2.1 for the
full project vocabulary; this table covers only the billing-specific ones.

| Name | Meaning |
|---|---|
| `total_venue_cost` | Total court rental for the season, entered once when the season is created |
| `total_games` | Number of games generated when the season is created |
| `member_count` | Number of fixed members, frozen for the season |
| `share_per_game` | The atomic money unit, see below |
| `billable_games` | Games that still have to be paid for, see Cancellation |
| `surplus` | Money collected above the venue cost by rounding up, see Surplus |

## Core formula

```
share_per_game = ceil(total_venue_cost / total_games / member_count)
```

Rounded up to whole dollars. This is the only rounding in the system.
Every other amount is a multiple of it:

```
member_season_fee = share_per_game * billable_games
drop_in_fee        = share_per_game
absence_refund     = share_per_game      # per covered absence
```

### Why rounding happens exactly once

Rounding the charge and the refund independently opens a gap, because the two
roundings can point in different directions. Example with
`total_venue_cost=10000`, `total_games=7`, `member_count=5`:

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
a game never changes it — only the multiplier changes. Payments already
recorded never need recalculating; a cancellation only affects the final
settlement.

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

With `total_venue_cost=10000`, `total_games=7`, `member_count=5`:

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

## Open questions

- Drop-in cancellation deadline, currently unlimited. Does cancelling late
  still incur the fee?
- Is there an absence deadline? Does a late absence forfeit the refund?
