from datetime import datetime
from decimal import Decimal

from volleyflow.ledger import EntryType, LedgerEntry, balance
from volleyflow.players import Player

ALICE = Player(id=1, name="Alice")
NOW = datetime(2026, 8, 1)


def test_balance_is_zero_with_no_entries():
    result = balance([])

    assert result == Decimal("0")


def test_balance_sums_a_charge_and_a_payment_to_zero():
    entries = [
        LedgerEntry(ALICE, EntryType.SEASON_FEE_CHARGED, Decimal("-2002"), NOW),
        LedgerEntry(ALICE, EntryType.PAYMENT, Decimal("2002"), NOW),
    ]

    result = balance(entries)

    assert result == Decimal("0")


def test_balance_reflects_an_absence_refund_not_yet_paid_out():
    entries = [
        LedgerEntry(ALICE, EntryType.SEASON_FEE_CHARGED, Decimal("-2002"), NOW),
        LedgerEntry(ALICE, EntryType.PAYMENT, Decimal("2002"), NOW),
        LedgerEntry(ALICE, EntryType.ABSENCE_REFUND, Decimal("286"), NOW),
    ]

    result = balance(entries)

    assert result == Decimal("286")


def test_cash_settlement_zeroes_a_positive_balance():
    entries = [
        LedgerEntry(ALICE, EntryType.ABSENCE_REFUND, Decimal("286"), NOW),
        LedgerEntry(ALICE, EntryType.PAYMENT, Decimal("-286"), NOW),
    ]

    result = balance(entries)

    assert result == Decimal("0")


def test_carried_over_entries_net_to_zero_across_seasons():
    entries = [
        LedgerEntry(ALICE, EntryType.CARRIED_OVER, Decimal("-150"), NOW),
        LedgerEntry(ALICE, EntryType.CARRIED_OVER, Decimal("150"), NOW),
    ]

    result = balance(entries)

    assert result == Decimal("0")
