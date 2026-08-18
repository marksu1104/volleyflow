"""Database engine and session setup.

Reads DATABASE_URL from the environment: from .env locally (via
python-dotenv), or a real environment variable once deployed. The lookup
is lazy — importing this module never requires DATABASE_URL to be set,
only actually calling get_engine()/get_session() does. That's what lets
SQLite-only tests import this file freely (see tests/db/test_models.py).
"""

import os
from functools import lru_cache

from dotenv import load_dotenv
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

load_dotenv()


@lru_cache
def get_engine() -> Engine:
    """Built once, reused for the app's lifetime.

    create_engine() sets up a connection pool; calling it fresh on every
    request (as this used to do) meant every request paid for a brand
    new TCP+TLS handshake to Neon before a single query could run.
    lru_cache turns this zero-argument function into a lazy singleton —
    still nothing happens at import time, only on the first real call.
    """
    return create_engine(os.environ["DATABASE_URL"])


def get_session() -> Session:
    return sessionmaker(bind=get_engine())()
