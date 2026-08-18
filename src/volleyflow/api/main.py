"""FastAPI application entry point."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from volleyflow.api.routes import router

app = FastAPI(title="VolleyFlow")

# The frontend (milestone 3) is plain static HTML/JS served from a
# different origin than this API — without CORS enabled, the browser
# blocks every fetch() call before it reaches a route. Wide open for
# now; this app has no cookies/session auth to leak, and it'll narrow
# to the real frontend origin once that's deployed (milestone 4).
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)
