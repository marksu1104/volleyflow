"""FastAPI application entry point."""

from fastapi import FastAPI

from volleyflow.api.routes import router

app = FastAPI(title="VolleyFlow")
app.include_router(router)
