"""FastAPI application entry point."""

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from volleyflow.api.error_reporting import ErrorReportingMiddleware, store_error_event
from volleyflow.api.routes import router

# Swagger UI and the OpenAPI schema map the whole API, so they are off
# unless VOLLEYFLOW_DOCS=1 (local only).
_DOCS_ENABLED = os.environ.get("VOLLEYFLOW_DOCS") == "1"

app = FastAPI(
    title="VolleyFlow",
    docs_url="/docs" if _DOCS_ENABLED else None,
    redoc_url="/redoc" if _DOCS_ENABLED else None,
    openapi_url="/openapi.json" if _DOCS_ENABLED else None,
)

# The frontend is served from GitHub Pages, a different origin.
_ALLOWED_ORIGINS = ["https://marksu1104.github.io"]

# Local pages on another port, or on a phone on the same network, are
# allowed only when local sign-in is on. Private IPv4 ranges only (RFC
# 1918).
_PRIVATE_LAN_ORIGIN = (
    r"http://(localhost|127\.0\.0\.1|\[::1\]"
    r"|10\.\d{1,3}\.\d{1,3}\.\d{1,3}"
    r"|192\.168\.\d{1,3}\.\d{1,3}"
    r"|172\.(1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3})(:\d+)?"
)
_dev_login = os.environ.get("VOLLEYFLOW_DEV_LOGIN") == "1"

# Order matters: middleware added later wraps middleware added earlier, so a
# caught error's response still passes out through CORS.
app.add_middleware(ErrorReportingMiddleware)
# Crashes are kept for the developer's overview, not only logged.
app.state.error_store = store_error_event
app.add_middleware(
    CORSMiddleware,
    allow_origins=_ALLOWED_ORIGINS,
    allow_origin_regex=_PRIVATE_LAN_ORIGIN if _dev_login else None,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)


@app.get("/health")
def health() -> dict[str, str]:
    """Liveness check. Touches no database, so pinging it to keep the
    free Render instance awake does not also wake Neon.
    """
    return {"status": "ok"}
