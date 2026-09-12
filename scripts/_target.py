"""Which database is this script about to touch, and how do we know?

Every script here reads `DATABASE_URL`, and until 2026-09-13 they each
printed the host and left it at that. Neon names every branch something
like `ep-dawn-pine-azbsvr6x`, so that line looks precise and tells you
nothing: the organizer ran `find_duplicates.py` expecting to learn
something about production and read a report about the dev branch
without either of us noticing for a message.

So the question a destructive script has to answer is not "what host"
but "where did this URL come from". `.env` is the dev branch by this
project's convention, and production is only ever reached by setting
`DATABASE_URL` in the shell on purpose. That distinction is knowable,
so it gets printed.
"""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import dotenv_values

DOTENV = Path(__file__).resolve().parent.parent / ".env"

DEV = "the dev branch (DATABASE_URL came from .env)"
ELSEWHERE = "NOT .env — DATABASE_URL was set in this shell"
UNSET = "DATABASE_URL is not set"


def describe() -> tuple[str, str]:
    """(host, where the URL came from) — both meant to be printed."""
    url = os.environ.get("DATABASE_URL")
    if not url:
        return ("?", UNSET)
    host = urlsplit(url).hostname or "?"
    # load_dotenv() does not override a variable already in the
    # environment, so an exact match means .env is what is in force.
    from_file = dotenv_values(DOTENV).get("DATABASE_URL") if DOTENV.exists() else None
    return (host, DEV if from_file and url == from_file else ELSEWHERE)


def announce(action: str) -> bool:
    """Prints the target. Returns whether it is the dev branch, so a
    caller can decide how much ceremony a destructive action needs."""
    host, source = describe()
    print(f"{action}: {host}")
    print(f"  {source}")
    return source == DEV
