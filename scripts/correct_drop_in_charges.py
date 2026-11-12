"""Brings every confirmed drop-in's charge to its night's current price,
for signups charged before charges were kept in step with the price.

    uv run python scripts/correct_drop_in_charges.py          # list them
    uv run python scripts/correct_drop_in_charges.py --apply  # correct them

Each correction is an adjustment entry, the same one the API now writes
whenever a night is re-priced; nothing already written is edited. A
settled season is listed but never changed: its books are closed.
Running it twice corrects nothing the second time.

Prints ids and amounts, never names: the workflow that runs it against
production (correct-drop-in-charges.yml) has public logs.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy.orm import Session

from volleyflow.api.routes._money import _stale_drop_in_charges, _sync_drop_in_charges
from volleyflow.db.engine import get_session
from volleyflow.db.models import GameRow, SeasonRow


@dataclass(frozen=True)
class Stale:
    season_id: int
    settled: bool
    drop_in_id: int
    player_id: int
    game_date: str
    charged: Decimal
    price: Decimal


def find(session: Session) -> list[Stale]:
    """Every drop-in charged something other than their night's price."""
    found: list[Stale] = []
    for season in session.query(SeasonRow).order_by(SeasonRow.id):
        for drop_in, price in _stale_drop_in_charges(session, season):
            assert drop_in.charged_amount is not None
            game = session.get(GameRow, drop_in.game_id)
            assert game is not None
            found.append(
                Stale(
                    season_id=season.id,
                    settled=season.settled_at is not None,
                    drop_in_id=drop_in.id,
                    player_id=drop_in.player_id,
                    game_date=game.date.isoformat(),
                    charged=drop_in.charged_amount,
                    price=price,
                )
            )
    return found


def correct(session: Session) -> int:
    """Corrects every unsettled season; the caller commits. Returns how
    many drop-ins it corrected."""
    stale = [s for s in find(session) if not s.settled]
    for season_id in sorted({s.season_id for s in stale}):
        season = session.get(SeasonRow, season_id)
        assert season is not None
        _sync_drop_in_charges(session, season)
    return len(stale)


def report(stale: list[Stale]) -> None:
    if not stale:
        print("Every drop-in is charged its night's price.")
        return
    print("season  drop-in  player  night       charged  price   difference")
    for s in stale:
        note = "  (settled: not changed)" if s.settled else ""
        print(
            f"{s.season_id:>6}  {s.drop_in_id:>7}  {s.player_id:>6}  {s.game_date}"
            f"  {s.charged:>7}  {s.price:>5}  {s.charged - s.price:>+10}{note}"
        )
    open_ = [s for s in stale if not s.settled]
    over = sum((s.charged - s.price for s in open_ if s.charged > s.price), Decimal(0))
    under = sum((s.price - s.charged for s in open_ if s.charged < s.price), Decimal(0))
    print(
        f"\n{len(open_)} to correct: {over} overcharged in all, "
        f"{under} undercharged in all."
    )


def main() -> None:
    apply = "--apply" in sys.argv[1:]
    session = get_session()
    try:
        stale = find(session)
        report(stale)
        if not apply:
            print("\nDry run: nothing written. Run with --apply to correct.")
            return
        corrected = correct(session)
        session.commit()
        print(f"\nCorrected {corrected} drop-in charge(s).")
        remaining = [s for s in find(session) if not s.settled]
        assert not remaining, f"still stale after correcting: {remaining}"
    finally:
        session.close()


if __name__ == "__main__":
    main()
