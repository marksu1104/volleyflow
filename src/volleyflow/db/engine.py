"""Database engine and session setup.

Reads DATABASE_URL from the environment: from .env locally (via
python-dotenv), or a real environment variable once deployed. The lookup
is lazy — importing this module never requires DATABASE_URL to be set,
only actually calling get_engine()/get_session() does. That's what lets
SQLite-only tests import this file freely (see tests/db/test_models.py).
"""

import os

from dotenv import load_dotenv
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

load_dotenv()


def get_engine() -> Engine:
    return create_engine(os.environ["DATABASE_URL"])


def get_session() -> Session:
    return sessionmaker(bind=get_engine())()
