"""FastAPI application entry point."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from volleyflow.api.line_webhook import router as line_webhook_router
from volleyflow.api.routes import router

app = FastAPI(title="VolleyFlow")

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
