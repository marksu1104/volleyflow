"""Shared fixtures.

Most DB-touching tests use an in-memory SQLite database: fast, no
network, a fresh schema per test. A handful of tests marked `postgres`
run against the real Neon database instead, to catch anything SQLite
lets slide (see pyproject.toml's marker registration).
"""

from collections.abc import Iterator
from typing import Any

import pytest
from sqlalchemy import Engine, create_engine, event
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

    # SQLite parses REFERENCES and then ignores it: foreign keys are off
    # unless every connection asks for them. Without this the whole API
    # suite — the fuzz sweep included — happily wrote rows pointing at
    # players that didn't exist, and the first anyone knew of it was a
    # 500 from Neon in front of a real organizer (2026-09-12,
    # season_members_player_id_fkey). A test database that enforces less
    # than the real one is a test database that certifies bugs.
    @event.listens_for(engine, "connect")
    def _enforce_foreign_keys(connection: Any, _record: Any) -> None:
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def db_session(sqlite_engine: Engine) -> Iterator[Session]:
    with Session(sqlite_engine) as session:
        yield session
