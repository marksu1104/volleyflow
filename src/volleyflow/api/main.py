"""FastAPI application entry point."""

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from volleyflow.api.error_reporting import ErrorReportingMiddleware
from volleyflow.api.line_webhook import router as line_webhook_router
from volleyflow.api.routes import router

# Swagger UI and the OpenAPI schema are a complete, browsable map of the
# API. Useful while developing, not something to leave open on a
# deployment holding real people's names and money — set VOLLEYFLOW_DOCS=1
# to turn them back on locally.
_DOCS_ENABLED = os.environ.get("VOLLEYFLOW_DOCS") == "1"

app = FastAPI(
    title="VolleyFlow",
    docs_url="/docs" if _DOCS_ENABLED else None,
    redoc_url="/redoc" if _DOCS_ENABLED else None,
    openapi_url="/openapi.json" if _DOCS_ENABLED else None,
)

# The frontend is plain static HTML/JS served from GitHub Pages — a
# different origin than this API — without CORS enabled, the browser
# blocks every fetch() call before it reaches a route.
_ALLOWED_ORIGINS = ["https://marksu1104.github.io"]

# The local pages are served from a different port than this API, so they
# are a different origin and need allowing too — but only when local
# sign-in is already on, which production never sets. One switch for the
# whole local setup rather than a second thing to remember.
#
# A regex rather than a list because the address isn't known in advance:
# testing on a real phone means serving to whatever 192.168.x.x the
# router handed out today. Confined to the three private IPv4 ranges
# (RFC 1918) — a public address never matches.
_PRIVATE_LAN_ORIGIN = (
    r"http://(localhost|127\.0\.0\.1|\[::1\]"
    r"|10\.\d{1,3}\.\d{1,3}\.\d{1,3}"
    r"|192\.168\.\d{1,3}\.\d{1,3}"
    r"|172\.(1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3})(:\d+)?"
)
_dev_login = os.environ.get("VOLLEYFLOW_DEV_LOGIN") == "1"

# Order matters: Starlette wraps middleware added *later* around
# middleware added earlier, so ErrorReportingMiddleware must be added
# before CORSMiddleware for a caught error's response to still pass back
# out through CORS and get its header — see ErrorReportingMiddleware's
# own docstring for what goes wrong the other way around.
app.add_middleware(ErrorReportingMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_ALLOWED_ORIGINS,
    allow_origin_regex=_PRIVATE_LAN_ORIGIN if _dev_login else None,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)
app.include_router(line_webhook_router)


@app.get("/health")
def health() -> dict[str, str]:
    """Liveness check — no DB touch on purpose, so pinging it to keep
    the free Render instance awake doesn't also load Neon every time.
    """
    return {"status": "ok"}
