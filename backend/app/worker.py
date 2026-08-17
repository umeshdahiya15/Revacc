"""Celery worker definition.

In production, pipeline steps execute as Celery tasks on a Redis-backed
queue, replacing the in-memory asyncio simulator. The simulator's `run_runner`
is called within each task so the wire protocol (WebSocket events, step
completion) remains identical.
"""
from __future__ import annotations

import os

from celery import Celery

celery_app = Celery(
    "mev_pipeline",
    broker=os.environ.get("REDIS_URL", "redis://localhost:6379/0"),
    backend=os.environ.get("REDIS_URL", "redis://localhost:6379/0"),
    include=["app.tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
)


@celery_app.task
def ping() -> str:
    """Health-check task."""
    return "pong"
