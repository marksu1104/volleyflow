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
from sqlalchemy.pool import StaticPool

from volleyflow.db.models import Base


@pytest.fixture
def sqlite_engine() -> Iterator[Engine]:
    # check_same_thread=False + StaticPool: API tests run the route inside
    # a worker thread (that's how FastAPI's TestClient works), but plain
    # in-memory SQLite is thread-affine and a fresh connection means a
    # fresh, empty :memory: database. StaticPool keeps exactly one
    # connection alive and shares it across threads.
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def db_session(sqlite_engine: Engine) -> Iterator[Session]:
    with Session(sqlite_engine) as session:
        yield session
