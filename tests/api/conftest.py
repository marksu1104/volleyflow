"""A TestClient wired to the SQLite test database instead of Neon.

FastAPI's dependency_overrides swaps get_db for a version that yields the
SQLite session from tests/conftest.py — routes.py never has to know.
"""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from volleyflow.api.dependencies import get_db
from volleyflow.api.main import app


@pytest.fixture
def client(db_session: Session) -> Iterator[TestClient]:
    def override_get_db() -> Iterator[Session]:
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    yield TestClient(app)
    app.dependency_overrides.clear()
