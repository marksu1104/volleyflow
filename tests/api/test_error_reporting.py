"""A 500 in production used to be invisible — see docs/backlog.md and
src/volleyflow/api/error_reporting.py for why. Two things are tested
here: that the middleware ordering in main.py doesn't quietly break CORS
on exactly the responses where a broken frontend most needs to read the
real error (see ErrorReportingMiddleware's docstring for the trap this
avoids), and that the organizer alert is rate-limited rather than able to
burn a month's LINE quota on one repeatedly-failing request.
"""

import time

import pytest
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient

from volleyflow.api import error_reporting, main, routes
from volleyflow.api.error_reporting import (
    ErrorReportingMiddleware,
    report_unhandled_error,
)


@pytest.fixture(autouse=True)
def _never_actually_push_to_line(monkeypatch: pytest.MonkeyPatch) -> None:
    """No test in this file may reach the real LINE API, independent of
    the production-only gate in report_unhandled_error.

    That gate depends on VOLLEYFLOW_DEV_LOGIN, which a plain `pytest` run
    does not set (only dev-api.ps1, for the live server, does) — so it
    would not have caught a test here doing the same thing. It already
    did, once: these tests deliberately trigger crashes, `.env` carries
    the same LINE credentials locally that production uses, and running
    this file paged the organizer's real phone. A test's side effects
    must never depend on which real-world switches happen to be set;
    each test that wants to see whether a push *would* happen still
    overrides push_to_user itself, deliberately.
    """
    monkeypatch.delenv("LINE_ORGANIZER_USER_ID", raising=False)
    # Also not left to whatever the ambient shell happens to have: a test
    # that wants to see the production gate itself sets this back on
    # deliberately (test_dev_login_suppresses_the_alert_even_when_
    # configured); every other test here is testing something else and
    # must not depend on it either way.
    monkeypatch.delenv("VOLLEYFLOW_DEV_LOGIN", raising=False)
    monkeypatch.setattr(
        error_reporting,
        "push_to_user",
        lambda *a, **k: pytest.fail(
            "a test reached the real LINE client — mock push_to_user explicitly"
        ),
    )


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


@pytest.fixture(autouse=True)
def _reset_rate_limit() -> None:
    error_reporting._last_reported.clear()


def test_dev_login_suppresses_the_alert_even_when_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The actual bug: .env carries the same LINE credentials locally that
    # production uses (reminders.py needs them to be testable by hand),
    # so with nothing else stopping it, every unhandled exception in
    # local development — a deliberately-broken test route among them —
    # paged the organizer's real phone. VOLLEYFLOW_DEV_LOGIN=1 is set by
    # dev-api.ps1 for exactly the local server this fires from, and
    # nowhere in production, so it's what this gates on.
    monkeypatch.setenv("VOLLEYFLOW_DEV_LOGIN", "1")
    monkeypatch.setenv("LINE_ORGANIZER_USER_ID", "U123")
    sent = []
    monkeypatch.setattr(
        error_reporting, "push_to_user", lambda uid, text: sent.append(text)
    )

    report_unhandled_error("/p", ValueError("bad value"))

    assert sent == [], "local development must never page the real organizer"


def test_reports_to_the_organizer_when_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LINE_ORGANIZER_USER_ID", "U123")
    sent = []
    monkeypatch.setattr(
        error_reporting, "push_to_user", lambda uid, text: sent.append((uid, text))
    )

    report_unhandled_error("/some/path", ValueError("bad value"))

    assert len(sent) == 1
    assert sent[0][0] == "U123"
    assert "ValueError" in sent[0][1]
    assert "/some/path" in sent[0][1]


def test_the_report_says_which_line_of_this_project_raised_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Without this the alert carried the exception's own text and nothing
    # else, cut at 200 characters — which for a database error is the
    # failing SQL and names no route at all. A 500 on
    # POST /seasons/{id}/members was reported three times before anyone
    # could say which line wrote the row.
    monkeypatch.setenv("LINE_ORGANIZER_USER_ID", "U123")
    sent = []
    monkeypatch.setattr(
        error_reporting, "push_to_user", lambda uid, text: sent.append(text)
    )

    try:
        # Raised from inside the package, so the frame walk has something
        # of this project's own to find — the point of _where is that it
        # skips the SQLAlchemy frames a bare traceback ends on.
        routes.list_clubs(db=None, current_player=None)  # type: ignore[arg-type]
    except Exception as exc:
        report_unhandled_error("/clubs", exc)

    assert "routes.py:" in sent[0]


def test_an_exception_from_outside_the_package_still_reports(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LINE_ORGANIZER_USER_ID", "U123")
    sent = []
    monkeypatch.setattr(
        error_reporting, "push_to_user", lambda uid, text: sent.append(text)
    )

    # No traceback at all — constructed, never raised. The location is
    # unknown and the alert must still go out saying so, rather than
    # failing and leaving the organizer with nothing.
    report_unhandled_error("/p", ValueError("nowhere in particular"))

    assert len(sent) == 1
    assert "ValueError" in sent[0]


def test_does_nothing_extra_without_an_organizer_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("LINE_ORGANIZER_USER_ID", raising=False)
    monkeypatch.setattr(
        error_reporting,
        "push_to_user",
        lambda *a: pytest.fail("must not attempt to push with nobody to push to"),
    )

    report_unhandled_error("/some/path", ValueError("bad value"))


def test_the_same_error_on_the_same_path_is_not_reported_twice_in_a_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LINE_ORGANIZER_USER_ID", "U123")
    sent = []
    monkeypatch.setattr(
        error_reporting, "push_to_user", lambda uid, text: sent.append(text)
    )

    report_unhandled_error("/p", ValueError("first"))
    report_unhandled_error("/p", ValueError("second"))
    report_unhandled_error("/p", ValueError("third"))

    assert len(sent) == 1, "a client retrying a broken request must not spend the quota"


def test_a_different_path_or_exception_type_gets_its_own_report(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LINE_ORGANIZER_USER_ID", "U123")
    sent = []
    monkeypatch.setattr(
        error_reporting, "push_to_user", lambda uid, text: sent.append(text)
    )

    report_unhandled_error("/p", ValueError("x"))
    report_unhandled_error("/q", ValueError("x"))
    report_unhandled_error("/p", KeyError("x"))

    assert len(sent) == 3, "different (type, path) pairs are different problems"


def test_the_report_is_allowed_again_after_the_rate_limit_passes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LINE_ORGANIZER_USER_ID", "U123")
    sent = []
    monkeypatch.setattr(
        error_reporting, "push_to_user", lambda uid, text: sent.append(text)
    )
    fake_now = [1000.0]
    monkeypatch.setattr(time, "monotonic", lambda: fake_now[0])

    report_unhandled_error("/p", ValueError("first"))
    fake_now[0] += error_reporting.RATE_LIMIT_SECONDS + 1
    report_unhandled_error("/p", ValueError("second"))

    assert len(sent) == 2


def test_a_failure_to_send_the_line_alert_does_not_propagate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Reporting the crash must never itself crash the request.
    monkeypatch.setenv("LINE_ORGANIZER_USER_ID", "U123")

    def _broken(*_args: object) -> None:
        raise RuntimeError("LINE is down too")

    monkeypatch.setattr(error_reporting, "push_to_user", _broken)

    report_unhandled_error("/p", ValueError("original problem"))
