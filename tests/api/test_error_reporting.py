"""A 500 in production used to be invisible — see
src/volleyflow/api/error_reporting.py for why. What is tested here is
that the middleware ordering in main.py doesn't quietly break CORS on
exactly the responses where a broken frontend most needs to read the
real error (see ErrorReportingMiddleware's docstring for the trap this
avoids), that the crash is written down with the line that raised it,
and that nothing is pushed to anybody over LINE.

The LINE crash report was removed on 2026-09-12 at the organizer's
request: unasked-for messages that meant nothing to the person getting
them, each one spending a push from the free tier's 200 a month.
"""

import logging

import pytest
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient

from volleyflow.api import error_reporting, main
from volleyflow.api.error_reporting import (
    ErrorReportingMiddleware,
    report_unhandled_error,
)
from volleyflow.api.routes import clubs


def _toy_app() -> FastAPI:
    """The same middleware order main.py uses, around a route that always
    throws — isolated from the real router so this doesn't need a
    deliberately-broken production endpoint to test against."""
    app = FastAPI()
    app.add_middleware(ErrorReportingMiddleware)
    app.add_middleware(CORSMiddleware, allow_origins=["https://example.com"])

    @app.get("/boom")
    def boom() -> None:
        raise RuntimeError("kaboom")

    return app


def test_middleware_order_in_the_real_app_matches_the_toy_app() -> None:
    # The toy app above is only a faithful test of main.py if it actually
    # mirrors main.py's registration order — this pins that down, so a
    # future reordering in main.py fails here rather than silently
    # reintroducing the CORS gap.
    names = [m.cls.__name__ for m in main.app.user_middleware]
    assert names.index("CORSMiddleware") < names.index("ErrorReportingMiddleware"), (
        "CORSMiddleware must be added after (and so wrap) ErrorReportingMiddleware"
    )


def test_an_unhandled_exception_still_gets_a_cors_header() -> None:
    # The trap this guards against: an exception handler registered for
    # the bare Exception class attaches to Starlette's ServerErrorMiddleware,
    # which sits outside CORS — so the response it builds never gets
    # Access-Control-Allow-Origin, and the browser reports a same-origin
    # violation instead of the real 500. Middleware ordered correctly
    # avoids it; this proves the response actually carries the header.
    client = TestClient(_toy_app(), raise_server_exceptions=False)

    response = client.get("/boom", headers={"Origin": "https://example.com"})

    assert response.status_code == 500
    assert response.headers.get("access-control-allow-origin") == "https://example.com"
    assert response.json() == {"detail": "Internal server error"}


def test_a_route_that_works_is_unaffected() -> None:
    app = FastAPI()
    app.add_middleware(ErrorReportingMiddleware)
    app.add_middleware(CORSMiddleware, allow_origins=["https://example.com"])

    @app.get("/fine")
    def fine() -> dict[str, str]:
        return {"ok": "yes"}

    client = TestClient(app)
    response = client.get("/fine", headers={"Origin": "https://example.com"})

    assert response.status_code == 200
    assert response.json() == {"ok": "yes"}
    assert response.headers.get("access-control-allow-origin") == "https://example.com"


def test_nothing_is_pushed_over_line_when_a_route_crashes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The organizer's phone must stay quiet.

    Not merely "the code no longer calls push_to_user" — that is one
    import away from coming back, and it came back once already in a
    different form (the group message). This asserts the module holds no
    reference to the LINE client at all, and that a crash with the
    organizer id configured still sends nothing.
    """
    monkeypatch.setenv("LINE_ORGANIZER_USER_ID", "U123")

    report_unhandled_error("/some/path", ValueError("bad value"))

    assert not hasattr(error_reporting, "push_to_user"), (
        "error_reporting must not reach for the LINE client at all"
    )


def test_the_log_line_names_the_route_and_the_line_that_raised_it(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # The one line of a traceback worth reading first. Without it the log
    # entry named only the request path, and a database error's own text
    # is the failing SQL — which identifies no route at all. A 500 on
    # POST /seasons/{id}/members was reported three times before anyone
    # could say which line wrote the row. The file it names is the one
    # the route now lives in — routes/clubs.py, since the split.
    try:
        # Raised from inside the package, so the frame walk has something
        # of this project's own to find — the point of _where is that it
        # skips the SQLAlchemy frames a bare traceback ends on.
        clubs.list_clubs(db=None, current_player=None)  # type: ignore[arg-type]
    except Exception as exc:
        with caplog.at_level(logging.ERROR, logger="volleyflow.errors"):
            report_unhandled_error("/clubs", exc)

    assert "/clubs" in caplog.text
    assert "clubs.py:" in caplog.text


def test_an_exception_with_no_traceback_still_gets_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Constructed, never raised, so there is no frame to name. The crash
    # must still be written down rather than the reporting itself failing.
    with caplog.at_level(logging.ERROR, logger="volleyflow.errors"):
        report_unhandled_error("/p", ValueError("nowhere in particular"))

    assert "unknown location" in caplog.text
    assert "ValueError" in caplog.text
