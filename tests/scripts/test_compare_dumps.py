"""compare_dumps.py: the restore drill's verdict, which must notice any
row that didn't survive and must print nothing a public log can't hold."""

import json
from pathlib import Path

import pytest

import scripts.compare_dumps as compare_dumps

PLAYER_A = {"id": 1, "name": "Sample Member", "line_user_id": None}
PLAYER_B = {"id": 2, "name": "Another Member", "line_user_id": "U0000"}
PAYMENT = {"id": 7, "player_id": 1, "amount": {"__decimal__": "205.00"}}


def _write(path: Path, dump: compare_dumps.Dump) -> Path:
    path.write_text(json.dumps(dump), encoding="utf-8")
    return path


def test_same_rows_in_another_order_match() -> None:
    before = {"players": [PLAYER_A, PLAYER_B], "ledger_entries": [PAYMENT]}
    after = {"players": [PLAYER_B, PLAYER_A], "ledger_entries": [PAYMENT]}

    differing = compare_dumps.differing_tables(before, after)

    assert differing == []


def test_one_changed_value_names_only_its_table() -> None:
    before = {"players": [PLAYER_A], "ledger_entries": [PAYMENT]}
    changed = {**PAYMENT, "amount": {"__decimal__": "205"}}
    after = {"players": [PLAYER_A], "ledger_entries": [changed]}

    differing = compare_dumps.differing_tables(before, after)

    assert differing == ["ledger_entries"]


def test_a_lost_row_names_its_table() -> None:
    before = {"players": [PLAYER_A, PLAYER_B]}
    after = {"players": [PLAYER_A]}

    differing = compare_dumps.differing_tables(before, after)

    assert differing == ["players"]


def test_a_table_missing_from_one_dump_is_named_even_when_empty() -> None:
    before: compare_dumps.Dump = {"players": [PLAYER_A], "audit_events": []}
    after: compare_dumps.Dump = {"players": [PLAYER_A]}

    differing = compare_dumps.differing_tables(before, after)

    assert differing == ["audit_events"]


def test_main_prints_table_names_but_no_values_or_counts(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rows = [PLAYER_A, PLAYER_B, {**PLAYER_A, "id": 3}]
    before = _write(tmp_path / "before.json", {"players": rows})
    after = _write(tmp_path / "after.json", {"players": rows[:2]})

    status = compare_dumps.main([str(before), str(after)])

    out = capsys.readouterr().out
    assert status == 1
    assert "players: DIFFERENT" in out
    assert "Sample Member" not in out
    assert "3" not in out and "2" not in out


def test_main_returns_zero_when_every_table_matches(tmp_path: Path) -> None:
    dump: compare_dumps.Dump = {"players": [PLAYER_A], "ledger_entries": [PAYMENT]}
    before = _write(tmp_path / "before.json", dump)
    after = _write(tmp_path / "after.json", dump)

    status = compare_dumps.main([str(before), str(after)])

    assert status == 0
