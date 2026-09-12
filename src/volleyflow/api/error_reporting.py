"""Catches an unhandled exception, writes it down, and answers the
browser without losing its own CORS headers.

It used to also push a crash report to the organizer over LINE. That is
gone, by the organizer's decision (2026-09-12): it is not a feature they
asked for, the messages arrived unprompted and meant nothing to the
person receiving them, and every one of them spent a push from the free
tier's 200 a month — the same allowance the short-roster alert needs.
Deleted rather than left behind a flag, for the same reason the group
message was: something nobody wants must not be one environment variable
away from starting again.

What a crash leaves behind now is a log line on the server, which is
where a crash report belongs, plus — locally only — the whole traceback
in the response body, which is what a browser-driven check can read and
a server console cannot.
"""

import logging
import os
import traceback
from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

logger = logging.getLogger("volleyflow.errors")


def _where(exc: BaseException) -> str:
    """The deepest line of this project's own code the exception passed
    through — "routes.py:2173 in add_member".

    The deepest volleyflow frame, not the deepest frame overall: the
    latter is always somewhere inside SQLAlchemy and names nothing that
    can be fixed here. Kept when the LINE report went, because it is the
    one line of a traceback worth reading first and it now leads the log
    entry instead.
    """
    here = ""
    tb = exc.__traceback__
    while tb is not None:
        frame = tb.tb_frame
        name = frame.f_globals.get("__name__", "")
        if name.startswith("volleyflow.") and not name.endswith("error_reporting"):
            here = f"{os.path.basename(frame.f_code.co_filename)}:{tb.tb_lineno}"
            here += f" in {frame.f_code.co_name}"
        tb = tb.tb_next
    return here or "(unknown location)"


def report_unhandled_error(path: str, exc: Exception) -> None:
    """Writes the crash to the log, naming the line of this project's own
    code that raised it before the traceback itself.

    Render keeps the service's log, so this is readable after the fact
    without anything being pushed at anyone.
    """
    logger.error("Unhandled error on %s (%s)", path, _where(exc), exc_info=exc)


class ErrorReportingMiddleware(BaseHTTPMiddleware):
    """Catches what FastAPI's own exception handling doesn't, and answers
    it, without losing CORS headers on the way out.

    A handler registered the ordinary way —
    `@app.exception_handler(Exception)` — is a well-known Starlette trap:
    it attaches to `ServerErrorMiddleware`, which sits *outside* every
    user-added middleware including CORS, so the response it builds never
    passes through `CORSMiddleware` and never gets an
    `Access-Control-Allow-Origin` header. The browser then reports a
    same-origin-policy violation instead of the actual 500 — which is
    exactly how linking a LINE account read in the browser on
    2026-09-12, when it crashed on a foreign key it hadn't accounted
    for (see docs/backlog.md). This is middleware, added before CORS in
    main.py, rather than an exception handler, precisely so a response
    built here still passes back out through CORS, which sits outside
    it, and gets the header normally.
    """

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        try:
            return await call_next(request)
        except Exception as exc:
            report_unhandled_error(request.url.path, exc)
            # Locally, the reason travels back with the response. The
            # server's own console is the usual place to read a traceback
            # and it is exactly the place a browser-driven test can't
            # look: tests/visual/smoke.js found a 500 three times and
            # could only report the status, which cost three sessions of
            # guessing. With this it handed over the whole thing in one
            # run. Never in production — a stack trace names table
            # columns and file paths.
            if os.environ.get("VOLLEYFLOW_DEV_LOGIN") == "1":
                return JSONResponse(
                    status_code=500,
                    content={
                        "detail": f"{type(exc).__name__}: {exc}",
                        "traceback": traceback.format_exc().splitlines()[-25:],
                    },
                )
            return JSONResponse(
                status_code=500, content={"detail": "Internal server error"}
            )
