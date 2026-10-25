"""Database engine and session setup.

DATABASE_URL comes from .env locally or the environment when deployed.
Read lazily, so importing this module never needs it; only
get_engine() and get_session() do.
"""

import os
from functools import lru_cache

from dotenv import load_dotenv
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

load_dotenv()

_PSYCOPG3 = "postgresql+psycopg://"


def database_url() -> str:
    """DATABASE_URL with the driver pinned to psycopg 3. Neon hands out
    `postgresql://`, which SQLAlchemy maps to psycopg2 (not
    installed), so every form is accepted and the string can be pasted
    as is.
    """
    url = os.environ["DATABASE_URL"]
    for prefix in ("postgresql://", "postgres://"):
        if url.startswith(prefix):
            return _PSYCOPG3 + url.removeprefix(prefix)
    return url


@lru_cache
def get_engine() -> Engine:
    """The engine, built once on first use and reused, so requests share
    a connection pool.

    `pool_pre_ping=True` tests each connection before handing it out:
    Neon's free tier suspends after a few idle minutes and drops every
    connection, and without the ping the first request after a quiet
    spell failed.
    """
    return create_engine(database_url(), pool_pre_ping=True)


def get_session() -> Session:
    return sessionmaker(bind=get_engine())()
