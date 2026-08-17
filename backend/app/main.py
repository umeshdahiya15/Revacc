"""Revacc API — FastAPI application entrypoint.

Run locally:
    uvicorn app.main:app --reload --port 8000
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import settings
from .routes import router, ws_router
from .simulator import engine

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("revacc")


@asynccontextmanager
async def lifespan(app: FastAPI):
    engine.start()
    logger.info("Revacc API ready — step tick = %sms", settings.step_tick_ms)
    yield
    await engine.stop()


app = FastAPI(
    title="Revacc API",
    description="14-phase reverse-vaccinology vaccine design backend.",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=list(settings.cors_origins),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router, prefix=settings.api_prefix)
app.include_router(ws_router)


@app.get("/")
def root() -> dict:
    return {"service": "revacc-api", "docs": "/docs", "health": "/api/health"}