"""A TestClient wired to the SQLite test database instead of Neon.

FastAPI's dependency_overrides swaps get_db for a version that yields the
SQLite session from tests/conftest.py — routes.py never has to know.
"""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from volleyflow.api import auth
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
    # "token" IS the line_user_id it verifies to.
    #
    # Patched on `auth`, where it is defined, which works only because
    # its callers import the *module* and call `auth.verify_id_token(...)`
    # rather than binding the name locally. That is deliberate on both
    # sides: two modules verify a token now — the Authorization header in
    # routes/_people.py, and the body of /players/identify in
    # routes/players.py — and patching importers one at a time means the
    # third one silently reaches the real LINE API.
    monkeypatch.setattr(auth, "verify_id_token", lambda token: token)

    app.dependency_overrides[get_db] = override_get_db
    yield TestClient(app)
    app.dependency_overrides.clear()
