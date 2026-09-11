"""Reports an unhandled exception to the organizer over LINE.

A 500 in production used to be invisible unless somebody happened to be
looking when it happened — found the hard way on 2026-09-12, when linking
a LINE account had been crashing on a foreign key for the whole life of
the project and nobody noticed (see docs/backlog.md). Render's free tier
has no error-tracking add-on, and standing up a third-party service for
a single-organizer club is a lot of new surface (a signup, a secret, a
dependency) for what this already has the pieces for: the LINE push
client this project uses for reminders can just as well carry a crash
report to the one person who can act on it.

Rate-limited per (exception type, request path) rather than sent every
time: LINE's free tier is 200 messages a month, shared with game
reminders, and a client retrying a broken endpoint in a loop could
otherwise spend the whole month's quota reporting the same bug over and
over. The limit is in-memory and resets on every process restart, which
on a free tier that sleeps between visits is often enough that this
isn't a real gap in practice — a bug that keeps happening keeps getting
reported, just not more than once every RATE_LIMIT_SECONDS.
"""

import logging
import os
import time
from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from volleyflow.notify.line_client import push_to_user

logger = logging.getLogger("volleyflow.errors")

RATE_LIMIT_SECONDS = 30 * 60
_last_reported: dict[str, float] = {}


def report_unhandled_error(path: str, exc: Exception) -> None:
    """Logs unconditionally; alerts the organizer at most once per
    (exception type, path) every RATE_LIMIT_SECONDS — and only outside
    local development.

    `.env` carries the same LINE credentials locally that production
    uses, because `reminders.py` needs them for anyone to test a
    reminder by hand. That is fine for a script a person runs on
    purpose; it is not fine for a handler that fires on every unhandled
    exception, and local development produces far more of those than
    production ever does — a deliberately-broken test route, a mistake
    while debugging. Gated on VOLLEYFLOW_DEV_LOGIN, the same flag
    main.py already uses to tell local from deployed: unset in
    production, always set locally (see dev-api.ps1). Found the hard
    way — see docs/dev-log.md, 2026-09-12 — when local debugging of this
    very file paged the organizer's real phone.
    """
    logger.error("Unhandled error on %s", path, exc_info=exc)

    if os.environ.get("VOLLEYFLOW_DEV_LOGIN") == "1":
        return

    key = f"{type(exc).__name__}:{path}"
    now = time.monotonic()
    last = _last_reported.get(key)
    # A key that has never been seen must always be reported, whatever
    # the clock happens to read — time.monotonic()'s epoch is arbitrary
    # (often process or system start), so "now" can be smaller than
    # RATE_LIMIT_SECONDS early in a process's life, and comparing against
    # a 0.0 default made that read as "already reported a moment ago".
    if last is not None and now - last < RATE_LIMIT_SECONDS:
        return
    _last_reported[key] = now

    organizer_id = os.environ.get("LINE_ORGANIZER_USER_ID")
    if not organizer_id:
        return
    try:
        push_to_user(
            organizer_id,
            f"系統發生錯誤：{path}\n{type(exc).__name__}: {str(exc)[:200]}",
        )
    except Exception:
        # Reporting the error must never itself crash the request that
        # triggered it, or fail so loudly it masks the original one.
        logger.exception("Also failed to send the LINE alert for the error above")


class ErrorReportingMiddleware(BaseHTTPMiddleware):
    """Catches what FastAPI's own exception handling doesn't, and reports
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
            return JSONResponse(
                status_code=500, content={"detail": "Internal server error"}
            )
