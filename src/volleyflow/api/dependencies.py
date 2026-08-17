"""FastAPI dependency-injection helpers."""

from collections.abc import Iterator

from sqlalchemy.orm import Session

from volleyflow.db.engine import get_session


def get_db() -> Iterator[Session]:
    """One session per request, always closed afterward."""
    session = get_session()
    try:
        yield session
    finally:
        session.close()
