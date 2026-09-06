"""A TestClient wired to the SQLite test database instead of Neon.

FastAPI's dependency_overrides swaps get_db for a version that yields the
SQLite session from tests/conftest.py — routes.py never has to know.
"""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from volleyflow.api import routes
from volleyflow.api.dependencies import get_db
from volleyflow.api.main import app


@pytest.fixture
def client(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> Iterator[TestClient]:
    def override_get_db() -> Iterator[Session]:
        yield db_session

    # Real LINE ID token verification means a real network call to LINE —
    # not available in tests. Faked as the identity function: a test
    # "token" IS the line_user_id it verifies to, same fakery pattern as
    # push_to_group/push_to_user in tests/notify/test_reminders.py.
    # Patched on `routes` (the importing module), not `auth` (where it's
    # defined) — `from ... import verify_id_token` binds a name in
    # routes's own namespace that a patch on the origin module wouldn't
    # reach.
    monkeypatch.setattr(routes, "verify_id_token", lambda token: token)

    app.dependency_overrides[get_db] = override_get_db
    yield TestClient(app)
    app.dependency_overrides.clear()
