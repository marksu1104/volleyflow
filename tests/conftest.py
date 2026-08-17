"""Shared fixtures.

Most DB-touching tests use an in-memory SQLite database: fast, no
network, a fresh schema per test. A handful of tests marked `postgres`
run against the real Neon database instead, to catch anything SQLite
lets slide (see pyproject.toml's marker registration).
"""

from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session

from volleyflow.db.models import Base


@pytest.fixture
def sqlite_engine() -> Iterator[Engine]:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def db_session(sqlite_engine: Engine) -> Iterator[Session]:
    with Session(sqlite_engine) as session:
        yield session
