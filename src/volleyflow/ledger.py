"""LedgerEntry and balance calculation: an append-only history per player.

See docs/billing-rules.md "Ledger" for why balances are computed, not
stored: entries are the only record, so the balance can always be traced
back to why it is what it is.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum

from volleyflow.players import Player


class EntryType(Enum):
    SEASON_FEE_CHARGED = "season_fee_charged"
    DROP_IN_FEE_CHARGED = "drop_in_fee_charged"
    ABSENCE_REFUND = "absence_refund"
    PAYMENT = "payment"
    CARRIED_OVER = "carried_over"


@dataclass(frozen=True)
class LedgerEntry:
    """One event in a player's history. Amount is signed from the player's
    point of view: positive means the organizer owes the player, negative
    means the player owes the organizer.
    """

    player: Player
    entry_type: EntryType
    amount: Decimal
    recorded_at: datetime


def balance(entries: Sequence[LedgerEntry]) -> Decimal:
    """A player's current balance: the sum of their entries, nothing more."""
    return sum((entry.amount for entry in entries), start=Decimal("0"))
