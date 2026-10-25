"""Catches an unhandled exception, logs it, keeps it for the developer's
overview, and answers the browser without losing CORS headers. Locally
the response also carries the traceback.
"""

import logging
import os
import traceback
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

from starlette.concurrency import run_in_threadpool
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from volleyflow.api.dependencies import get_db
from volleyflow.db.models import ErrorEventRow

logger = logging.getLogger("volleyflow.errors")

KEEP_ERRORS = timedelta(days=30)


def _where(exc: BaseException) -> str:
    """The deepest line of this project's own code the exception passed
    through ("season_roster.py:120 in add_member"); the deepest frame
    overall is always inside a library.
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
    """Logs the crash, naming the project line that raised it before the
    traceback.
    """
    logger.error("Unhandled error on %s (%s)", path, _where(exc), exc_info=exc)


def store_error_event(request: Request, exc: Exception) -> None:
    """Keeps the crash as a row for the developer's overview and drops
    rows older than KEEP_ERRORS.

    Uses the app's own get_db (honouring a test override), rolled back
    first in case the crash left it mid-transaction. Never raises:
    failing to record an error must not cause a second one.
    """
    provider = request.app.dependency_overrides.get(get_db, get_db)
    sessions = provider()
    try:
        db = next(sessions)
        db.rollback()
        now = datetime.now(UTC).replace(tzinfo=None)
        referer = request.headers.get("referer") or ""
        page = urlsplit(referer).path.rsplit("/", 1)[-1] or None
        db.add(
            ErrorEventRow(
                occurred_at=now,
                method=request.method,
                path=request.url.path,
                page=page,
                error_type=type(exc).__name__,
                message=str(exc)[:500],
                location=_where(exc),
            )
        )
        db.query(ErrorEventRow).filter(
            ErrorEventRow.occurred_at < now - KEEP_ERRORS
        ).delete()
        db.commit()
    except Exception:
        logger.exception("Could not store the error event")
    finally:
        sessions.close()


class ErrorReportingMiddleware(BaseHTTPMiddleware):
    """Answers unhandled exceptions without losing CORS headers.

    A handler registered with `@app.exception_handler(Exception)`
    attaches to Starlette's ServerErrorMiddleware, outside
    CORSMiddleware, so its response has no Access-Control-Allow-Origin
    header and the browser reports a CORS error instead of the 500. As
    a middleware added before CORS, the response here passes back out
    through it.
    """

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        try:
            return await call_next(request)
        except Exception as exc:
            report_unhandled_error(request.url.path, exc)
            # Stored where the app provides a store (main.py). In a worker
            # thread, so a store waiting on a dead database does not stall
            # the event loop.
            store = getattr(request.app.state, "error_store", None)
            if store is not None:
                await run_in_threadpool(store, request, exc)
            # Locally the traceback travels back in the response, where a
            # browser-driven check can read it. Never in production: it
            # names tables and file paths.
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
