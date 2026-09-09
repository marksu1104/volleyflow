"""The connection string, and the driver it implies.

One line of config, but getting it wrong fails at connect time with
ModuleNotFoundError rather than anywhere near the paste that caused it —
which is exactly how it took a production deploy down.
"""

import pytest

from volleyflow.db.engine import database_url


def test_neons_own_scheme_is_pointed_at_the_installed_driver(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The one that matters. Neon's console hands out `postgresql://`,
    # which SQLAlchemy resolves to psycopg2 — not what this project
    # installs. Pasting it as-is has to keep working.
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@host/db?sslmode=require")

    assert database_url() == "postgresql+psycopg://u:p@host/db?sslmode=require"


def test_the_heroku_style_scheme_is_handled_too(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgres://u:p@host/db")

    assert database_url() == "postgresql+psycopg://u:p@host/db"


def test_a_url_that_already_names_the_driver_is_left_alone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@host/db")

    assert database_url() == "postgresql+psycopg://u:p@host/db"


def test_a_different_database_is_not_rewritten(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The SQLite-only tests hand this module their own URL.
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")

    assert database_url() == "sqlite:///:memory:"


def test_the_rewrite_does_not_touch_a_password_containing_the_scheme(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # removeprefix, not replace — a password is attacker-shaped input as
    # far as string surgery is concerned.
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:postgresql://@host/db")

    assert database_url() == "postgresql+psycopg://u:postgresql://@host/db"
