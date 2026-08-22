"""Application settings. Override via environment variables."""
from __future__ import annotations

import os
from dataclasses import dataclass, field


def _parse_cors() -> tuple[str, ...]:
    """Parse comma-separated CORS origins from ``MEV_CORS_ORIGINS``.

    Local origins remain available by default. Production deployments must
    explicitly provide the frontend origin; an explicit ``*`` is retained as
    an opt-in development setting for the existing local start script.
    """
    extra = os.environ.get("MEV_CORS_ORIGINS", "")
    if extra.strip() == "*":
        return ("*",)

    base = [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:8000",
        "http://127.0.0.1:8000",
        "https://localhost:3000",
        "https://127.0.0.1:3000",
    ]
    base.extend(
        origin.strip()
        for origin in extra.split(",")
        if origin.strip() and origin.strip() != "*"
    )
    return tuple(base)


@dataclass(frozen=True)
class Settings:
    api_prefix: str = "/api"
    cors_origins: tuple[str, ...] = field(default_factory=_parse_cors)
    # Milliseconds between step ticks while a job is running. Speeds up the
    # demo enormously; a real pipeline would block on tool I/O instead.
    step_tick_ms: int = int(os.environ.get("MEV_STEP_TICK_MS", "650"))
    max_steps_per_tick: int = int(os.environ.get("MEV_MAX_STEPS_PER_TICK", "1"))
    event_history: int = 500
    # Postgres DSN placeholder — reserved for Sprint 1 (Celery + SQLAlchemy).
    database_url: str = os.environ.get("MEV_DATABASE_URL", "postgresql://mev:mev@localhost:5432/mev")


settings = Settings()