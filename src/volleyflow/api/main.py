"""FastAPI application entry point."""

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

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
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://marksu1104.github.io"],
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
