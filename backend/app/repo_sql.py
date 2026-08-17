"""SQLAlchemy async repository — PostgreSQL backend.

Drop-in replacement for the in-memory `Repo` in `repo.py`. Uses async
SQLAlchemy with asyncpg driver. The router and simulator only call methods
on the `Repo` protocol, so swapping stays localized.

Tables:
  - jobs: full job JSON (config, phases, funnel, epitopes) as JSONB
  - events: pipeline event log per job

Usage:
  Set MEV_DATABASE_URL=postgresql+asyncpg://user:pass@host:5432/dbname
  Then: from app.repo_sql import repo as sql_repo
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import JSON, Column, DateTime, MetaData, Table, select, delete
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import declarative_base, sessionmaker

from .config import settings
from .models import Job, PipelineEvent

metadata = MetaData(schema="mev")

jobs_table = Table(
    "jobs",
    metadata,
    Column("id", PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
    Column("data", JSONB, nullable=False),
    Column("created_at", DateTime, default=lambda: datetime.now(timezone.utc)),
    Column("updated_at", DateTime, default=lambda: datetime.now(timezone.utc)),
)

events_table = Table(
    "events",
    metadata,
    Column("id", PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
    Column("job_id", PG_UUID(as_uuid=True), nullable=False, index=True),
    Column("data", JSON, nullable=False),
    Column("created_at", DateTime, default=lambda: datetime.now(timezone.utc)),
)

Base = declarative_base(metadata=metadata)
_async_session: Optional[sessionmaker] = None


def _get_engine():
    url = settings.database_url
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+asyncpg://")
    return create_async_engine(url, echo=False, future=True)


async def _init_db():
    """Create tables if they don't exist."""
    global _async_session
    engine = _get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(metadata.create_all)
    _async_session = sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def get_session() -> AsyncSession:
    if _async_session is None:
        await _init_db()
    return _async_session()  # type: ignore


class SQLRepo:
    """Async PostgreSQL repository."""

    async def create(self, job_create, job_id: Optional[str] = None) -> Job:
        from .repo import build_job
        job = build_job(job_id or f"job-{uuid.uuid4().hex[:8]}", job_create)
        session = await get_session()
        stmt = jobs_table.insert().values(id=job.id, data=job.model_dump(mode="json"))
        await session.execute(stmt)
        await session.commit()
        await session.close()
        return job

    async def get(self, job_id: str) -> Optional[Job]:
        session = await get_session()
        stmt = select(jobs_table).where(jobs_table.c.id == job_id)
        result = await session.execute(stmt)
        row = result.fetchone()
        await session.close()
        if row is None:
            return None
        return Job.model_validate(row.data)

    async def upsert(self, job: Job) -> Job:
        session = await get_session()
        stmt = (
            jobs_table.update()
            .where(jobs_table.c.id == job.id)
            .values(data=job.model_dump(mode="json"), updated_at=datetime.now(timezone.utc))
        )
        await session.execute(stmt)
        await session.commit()
        await session.close()
        return job

    async def list(self) -> list[Job]:
        session = await get_session()
        stmt = select(jobs_table)
        result = await session.execute(stmt)
        rows = result.fetchall()
        await session.close()
        return [Job.model_validate(row.data) for row in rows]

    async def delete(self, job_id: str) -> bool:
        session = await get_session()
        stmt = delete(jobs_table).where(jobs_table.c.id == job_id)
        result = await session.execute(stmt)
        await session.commit()
        await session.close()
        return result.rowcount > 0

    async def push_event(self, event: PipelineEvent) -> None:
        session = await get_session()
        stmt = events_table.insert().values(
            job_id=event.jobId,
            data=event.model_dump(mode="json"),
        )
        await session.execute(stmt)
        await session.commit()
        await session.close()

    async def events(self, job_id: str, since_id: Optional[str] = None) -> list[PipelineEvent]:
        session = await get_session()
        stmt = select(events_table).where(events_table.c.job_id == job_id)
        result = await session.execute(stmt)
        rows = result.fetchall()
        await session.close()
        events = [PipelineEvent.model_validate(row.data) for row in rows]
        if since_id:
            for i, event in enumerate(events):
                if event.id == since_id:
                    return events[i + 1:]
        return events

    async def seed(self, job: Job) -> None:
        session = await get_session()
        stmt = jobs_table.insert().values(id=job.id, data=job.model_dump(mode="json"))
        await session.execute(stmt)
        await session.commit()
        await session.close()


repo_sql = SQLRepo()
