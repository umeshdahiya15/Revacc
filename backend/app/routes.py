"""REST + WebSocket routes. Everything is mounted under /api except /ws."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState

from .models import Epitope, Job, JobCreate, PipelineEvent
from .repo import repo
from .simulator import _recompute_aggregates, _sync_phase_status, engine
from .ws import manager

router = APIRouter()
ws_router = APIRouter()

_ACTIVITY_KIND = {
    "step_started": "running",
    "step_progress": "running",
    "step_completed": "success",
    "step_failed": "error",
    "filter_applied": "success",
    "pipeline_paused": "warning",
    "pipeline_resumed": "info",
    "pipeline_completed": "success",
}


# ---------------------------------------------------------------------------
# Health / discovery
# ---------------------------------------------------------------------------
@router.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "service": "revacc-api",
        "websocket": "/ws/pipeline/{job_id}",
        "jobCount": len(repo.list()),
    }


# ---------------------------------------------------------------------------
# Activity feed (recent events across all jobs)
# ---------------------------------------------------------------------------
@router.get("/activity")
def recent_activity(limit: int = 20) -> list[dict]:
    """Aggregate the most recent pipeline events across all jobs.

    Mirrors the frontend `ActivityEntry` contract so the dashboard can render
    a real feed without any mock data.
    """
    job_names = {job.id: job.name for job in repo.list()}
    entries: list[dict] = []
    for job in repo.list():
        for event in repo.events(job.id):
            kind = _ACTIVITY_KIND.get(event.type, "info")
            message = _activity_message(event)
            entries.append(
                {
                    "id": event.id,
                    "jobId": job.id,
                    "jobName": job_names.get(job.id, job.id),
                    "message": message,
                    "timestamp": event.timestamp,
                    "kind": kind,
                }
            )
    entries.sort(key=lambda e: e["timestamp"], reverse=True)
    return entries[: max(1, limit)]


def _activity_message(event: PipelineEvent) -> str:
    step = f"{event.phase}.{event.step}" if event.phase and event.step else ""
    tool = f" · {event.tool}" if event.tool else ""
    if event.type in ("step_started", "step_progress") and step:
        return f"Step {step} ({event.tool or 'tool'}) {'started' if event.type == 'step_started' else 'progressing'}"
    if event.type == "step_completed" and step:
        suffix = f" · {event.duration}s" if event.duration else ""
        return f"Step {step} completed{tool}{suffix}"
    if event.type == "step_failed" and step:
        return f"Step {step} failed{tool}: {event.error or 'unknown error'}"
    if event.type == "pipeline_paused":
        return f"Pipeline paused — {event.message or 'step review required'}"
    if event.type == "pipeline_resumed":
        return f"Pipeline resumed — {event.message or 'continuing'}"
    if event.type == "pipeline_completed":
        return "Pipeline completed — all phases finished"
    if event.type == "filter_applied":
        return "Filter applied — funnel counts updated"
    return event.message or event.type


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------
@router.get("/jobs", response_model=list[Job])
def list_jobs() -> list[Job]:
    return repo.list()


@router.post("/jobs", response_model=Job, status_code=201)
def create_job(create: JobCreate) -> Job:
    return repo.create(create)


@router.get("/jobs/{job_id}", response_model=Job)
def get_job(job_id: str) -> Job:
    job = repo.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    return job


@router.delete("/jobs/{job_id}", status_code=204)
def delete_job(job_id: str) -> None:
    if not repo.delete(job_id):
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")


# ---------------------------------------------------------------------------
# Job lifecycle control
# ---------------------------------------------------------------------------
@router.post("/jobs/{job_id}/start", response_model=Job)
async def start_job(job_id: str) -> Job:
    job = repo.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    if job.status == "completed":
        raise HTTPException(status_code=409, detail="Job already completed")
    job.status = "running"
    job = await engine.reconcile_on_start(job)
    await _broadcast_control(job_id, "pipeline_resumed", message="started")
    return job


@router.post("/jobs/{job_id}/resume", response_model=Job)
async def resume_job(job_id: str) -> Job:
    job = repo.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    if job.status != "paused":
        raise HTTPException(status_code=409, detail="Job is not paused")
    job.status = "running"
    job = await engine.reconcile_on_start(job)
    await _broadcast_control(job_id, "pipeline_resumed", message="resumed")
    return job


@router.post("/jobs/{job_id}/pause", response_model=Job)
async def pause_job(job_id: str) -> Job:
    job = repo.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    if job.status != "running":
        raise HTTPException(status_code=409, detail="Job is not running")
    job.status = "paused"
    job = repo.upsert(job)
    await _broadcast_control(job_id, "pipeline_paused", message="paused")
    return job


@router.post("/jobs/{job_id}/stop", response_model=Job)
async def stop_job(job_id: str) -> Job:
    return await pause_job(job_id)


# ---------------------------------------------------------------------------
# Event history (polling fallback for the frontend)
# ---------------------------------------------------------------------------
@router.get("/jobs/{job_id}/events", response_model=list[PipelineEvent])
def job_events(job_id: str, since_id: str | None = None) -> list[PipelineEvent]:
    if repo.get(job_id) is None:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    return repo.events(job_id, since_id=since_id)


# ---------------------------------------------------------------------------
# Per-step lifecycle: retry / skip
# ---------------------------------------------------------------------------
def _find_step(job: Job, step_id: str):
    for phase in job.phases:
        for step in phase.steps:
            if step.id == step_id:
                return phase, step
    return None, None


@router.post("/jobs/{job_id}/steps/{step_id}/retry", response_model=Job)
async def retry_step(job_id: str, step_id: str) -> Job:
    job = repo.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    _, step = _find_step(job, step_id)
    if step is None:
        raise HTTPException(status_code=404, detail=f"Step {step_id} not found")
    if step.status not in ("failed", "paused"):
        raise HTTPException(status_code=409, detail=f"Step {step_id} is not retryable (status={step.status})")

    # Reset so the engine re-runs the real tool on the next tick.
    step.status = "pending"
    step.percent = None
    step.error = None
    step.result = None
    step.completedAt = None
    job.status = "running"
    job = _sync_phase_status(_recompute_aggregates(job))
    repo.upsert(job)
    await _broadcast_control(job_id, "pipeline_resumed", message=f"retrying step {step_id}")
    return job


@router.post("/jobs/{job_id}/steps/{step_id}/skip", response_model=Job)
async def skip_step(job_id: str, step_id: str) -> Job:
    job = repo.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    _, step = _find_step(job, step_id)
    if step is None:
        raise HTTPException(status_code=404, detail=f"Step {step_id} not found")
    if step.status not in ("failed", "paused", "pending", "running"):
        raise HTTPException(status_code=409, detail=f"Step {step_id} is not skippable (status={step.status})")

    step.status = "skipped"
    step.percent = None
    step.error = None
    step.completedAt = datetime.now(timezone.utc).isoformat()
    if job.status != "completed":
        job.status = "running"
    job = _sync_phase_status(_recompute_aggregates(job))
    repo.upsert(job)
    await _broadcast_control(job_id, "pipeline_resumed", message=f"skipped step {step_id}")
    return job


@router.get("/jobs/{job_id}/epitopes", response_model=list[Epitope])
def job_epitopes(job_id: str, type: str | None = None) -> list[Epitope]:
    job = repo.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    if type:
        return [e for e in job.epitopes if e.type == type]
    return job.epitopes


async def _broadcast_control(job_id: str, kind: str, message: str) -> None:
    event = PipelineEvent(
        id=f"evt-{job_id}-{kind}",
        jobId=job_id,
        type=kind,  # type: ignore[arg-type]
        message=message,
        timestamp=datetime.now(timezone.utc).isoformat(),
    )
    repo.push_event(event)
    await manager.broadcast(job_id, event.model_dump(mode="json"))


# ---------------------------------------------------------------------------
# WebSocket — /ws/pipeline/{job_id}
# ---------------------------------------------------------------------------
@ws_router.websocket("/ws/pipeline/{job_id}")
async def pipeline_ws(websocket: WebSocket, job_id: str) -> None:
    await manager.connect(job_id, websocket)
    try:
        # Replay recent history so late joiners catch up.
        for event in repo.events(job_id)[-20:]:
            if websocket.application_state == WebSocketState.CONNECTED:
                await websocket.send_json(event.model_dump(mode="json"))
        while True:
            # Keep the socket alive; control could later be sent over WS.
            message = await websocket.receive_text()
            if message:
                await websocket.send_json({"type": "ack", "received": message})
    except WebSocketDisconnect:
        await manager.disconnect(job_id, websocket)
    except Exception:  # pragma: no cover
        await manager.disconnect(job_id, websocket)