"""Thread-safe in-memory job repository.

Sprint 1 will swap this for PostgreSQL (SQLAlchemy async); the router and
simulator only touch `Repo`, so the swap stays localised.
"""
from __future__ import annotations

import threading
import uuid
from datetime import datetime, timezone
from typing import Optional

from .models import Job, JobCreate, PipelineEvent
from .pipeline import PHASES, TOTAL_STEPS, step_id


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def build_job(job_id: str, create: JobCreate) -> Job:
    """Assemble a canonical 14-phase Job with all 41 steps pending."""
    name = create.name or f"{create.pathogenName} MEV"

    phases = []
    for phase_no, (phase_name, steps) in enumerate(PHASES, start=1):
        phase_steps = [
            {
                "id": step_id(phase_no, step_no),
                "phase": phase_no,
                "number": step_no,
                "name": step_name,
                "tool": tool,
                "status": "pending",
            }
            for step_no, (step_name, tool) in enumerate(steps, start=1)
        ]
        phases.append(
            {
                "number": phase_no,
                "name": phase_name,
                "status": "pending",
                "steps": phase_steps,
            }
        )

    return Job(
        id=job_id,
        name=name,
        pathogenName=create.pathogenName,
        strain=create.strain,
        taxonId=create.taxonId,
        status="created",
        currentPhase=1,
        currentStep=1,
        progress=0,
        stepsCompleted=0,
        totalSteps=TOTAL_STEPS,
        elapsedSeconds=0,
        estimatedRemaining=None,
        createdAt=now_iso(),
        updatedAt=now_iso(),
        config={
            "pathogenName": create.pathogenName,
            "strain": create.strain,
            "taxonId": create.taxonId,
            "source": "pathogen",
            "fastaFileName": create.fastaFileName,
            "adjuvant": create.adjuvant,
            "cdHitThreshold": create.cdHitThreshold,
            "vaxijenThreshold": create.vaxijenThreshold,
            "expressionHost": create.expressionHost,
            "expressionVector": create.expressionVector,
            "runImmuneSim": create.runImmuneSim,
            "runDisulfide": create.runDisulfide,
            "enableCoverage": create.enableCoverage,
            "hlaMhc1": create.hlaMhc1 or [],
            "hlaMhc2": create.hlaMhc2 or [],
            "mhciPercentile": create.mhciPercentile,
            "mhciiPercentile": create.mhciiPercentile,
            "bCellWindow": create.bCellWindow,
            "coverageRegions": create.coverageRegions or [],
            "realTools": create.realTools,
            "reviewedOnly": create.reviewedOnly,
        },
        phases=phases,
    )


class Repo:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._jobs: dict[str, Job] = {}
        self._events: dict[str, list[PipelineEvent]] = {}

    # ---- jobs ----------------------------------------------------------
    def create(self, create: JobCreate, job_id: Optional[str] = None) -> Job:
        with self._lock:
            job = build_job(job_id or f"job-{uuid.uuid4().hex[:8]}", create)
            self._jobs[job.id] = job
            self._events[job.id] = []
            started = job.model_dump(mode="json")
            self._jobs[job.id] = Job.model_validate(started)
            return self._jobs[job.id]

    def seed(self, job: Job) -> None:
        with self._lock:
            self._jobs[job.id] = job
            self._events.setdefault(job.id, [])

    def get(self, job_id: str) -> Optional[Job]:
        with self._lock:
            return self._jobs.get(job_id)

    def list(self) -> list[Job]:
        with self._lock:
            return list(self._jobs.values())

    def upsert(self, job: Job) -> Job:
        with self._lock:
            job.updatedAt = now_iso()
            self._jobs[job.id] = job
            return job

    def delete(self, job_id: str) -> bool:
        with self._lock:
            removed = self._jobs.pop(job_id, None)
            self._events.pop(job_id, None)
            return removed is not None

    # ---- events --------------------------------------------------------
    def push_event(self, event: PipelineEvent) -> None:
        with self._lock:
            history = self._events.setdefault(event.jobId, [])
            history.append(event)
            if len(history) > 1000:
                del history[: len(history) - 1000]

    def events(self, job_id: str, since_id: Optional[str] = None) -> list[PipelineEvent]:
        with self._lock:
            history = self._events.get(job_id, [])
            if since_id:
                for i, event in enumerate(history):
                    if event.id == since_id:
                        return history[i + 1 :]
            return list(history)


repo = Repo()
