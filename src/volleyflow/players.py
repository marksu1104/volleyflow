"""Player: who someone is, for life.

See docs/billing-rules.md and CLAUDE.md section 2.1 for the vocabulary.
A season's fixed-member relationship (Membership) isn't a separate type —
it's just a Player appearing in Season.members (see schedule.py). It has
no data of its own to justify a wrapper type.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Player:
    """One real person. Exists once, forever, regardless of member status."""

    id: int
    name: str
