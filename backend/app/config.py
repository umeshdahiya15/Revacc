"""Application settings. Override via environment variables."""
from __future__ import annotations

import os
from dataclasses import dataclass, field


def _parse_cors() -> tuple[str, ...]:
    """Parse CORS origins from MEV_CORS_ORIGINS env var (comma-separated).

    In development, allow all origins so that tunneled frontends (Cloudflare,
    ngrok, etc.) can reach the API without manual CORS configuration.
    """
    extra = os.environ.get("MEV_CORS_ORIGINS", "")
    base = [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:8000",
        "http://127.0.0.1:8000",
        "https://localhost:3000",
        "https://127.0.0.1:3000",
    ]
    if extra:
        base.extend(o.strip() for o in extra.split(",") if o.strip())
    else:
        # Allow all origins when no explicit list is provided — safe for
        # local development and tunnelled setups (Cloudflare, ngrok).
        base.append("*")
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