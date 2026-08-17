"""Application settings. Override via environment variables."""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    api_prefix: str = "/api"
    cors_origins: tuple[str, ...] = (
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:8000",
        "http://127.0.0.1:8000",
    )
    # Milliseconds between step ticks while a job is running. Speeds up the
    # demo enormously; a real pipeline would block on tool I/O instead.
    step_tick_ms: int = int(os.environ.get("MEV_STEP_TICK_MS", "650"))
    max_steps_per_tick: int = int(os.environ.get("MEV_MAX_STEPS_PER_TICK", "1"))
    event_history: int = 500
    # Postgres DSN placeholder — reserved for Sprint 1 (Celery + SQLAlchemy).
    database_url: str = os.environ.get("MEV_DATABASE_URL", "postgresql://mev:mev@localhost:5432/mev")


settings = Settings()