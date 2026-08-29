"""REST + WebSocket routes. Everything is mounted under /api except /ws."""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
from urllib.parse import urlparse
from uuid import uuid4

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState

from .models import Epitope, Job, JobCreate, MEVStructureInput, PipelineEvent
from .repo import repo
from .simulator import _recompute_aggregates, _sync_phase_status, engine
from .tools.runner import get_session, get_session_peek
from .ws import manager

router = APIRouter()
ws_router = APIRouter()
_BACKEND_INSTANCE_ID = uuid4().hex[:12]

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


def _public_job(job: Job) -> Job:
    """Clone a Job with an allowlisted official-lifecycle status projection.

    Regular and manual-attachment step results are deliberately untouched.
    When an official lifecycle record exists, its complete provider-facing
    result and any accompanying internal error are replaced only in the API
    copy. The persisted job remains available for safe server-side resume.
    """
    from .tools.swissmodel_lifecycle import public_swissmodel_lifecycle_status

    public = job.model_copy(deep=True)
    for phase in public.phases:
        for step in phase.steps:
            result = step.result
            if not isinstance(result, dict) or "officialLifecycle" not in result:
                continue
            lifecycle = public_swissmodel_lifecycle_status(result.get("officialLifecycle"))
            step.result = {"officialLifecycle": lifecycle}
            if step.error is not None:
                message = lifecycle.get("message")
                step.error = step.error.model_copy(update={
                    "message": message if isinstance(message, str) else "Official lifecycle status is unavailable.",
                    "tool": "SWISS-MODEL",
                    "firstFailedAt": None,
                    "lastFailedAt": None,
                })
    return public


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
        "backendInstanceId": _BACKEND_INSTANCE_ID,
        "sessionStore": "in-memory",
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
    return [_public_job(job) for job in repo.list()]


@router.post("/jobs", response_model=Job, status_code=201)
def create_job(create: JobCreate) -> Job:
    if create.source == "pathogen" and create.taxonId is None:
        raise HTTPException(
            status_code=422,
            detail="Pathogen source requires the submitted organism's UniProt taxonomy ID.",
        )
    if create.source == "fasta":
        if not create.fastaText or not create.fastaText.strip():
            raise HTTPException(status_code=422, detail="FASTA source requires FASTA file contents.")
        if not create.fastaText.lstrip().startswith(">"):
            raise HTTPException(status_code=422, detail="FASTA upload does not contain a FASTA header.")
    job = repo.create(create)
    if create.source == "fasta" and create.fastaText:
        get_session(job.id)["fasta_text"] = create.fastaText
    return _public_job(job)




@router.get("/jobs/{job_id}/structure/requirements")
def structure_requirements(job_id: str) -> dict:
    """Return the exact sequence and attachment contract for Phase 11-2."""
    job = repo.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    step = next((
        step for phase in job.phases for step in phase.steps if step.id == "9-2"
    ), None)
    result = step.result if step and isinstance(step.result, dict) else {}
    sequence = str(result.get("sequence") or "").replace(" ", "").upper()
    if not sequence:
        raise HTTPException(
            status_code=409,
            detail="MEV assembly (Step 9-2) must complete before a structure can be submitted.",
        )
    session = get_session_peek(job.id) or {}
    structure_input = session.get("mev_structure_input")
    return {
        "jobId": job.id,
        "step": "11-2",
        "sequence": sequence,
        "sequenceLength": len(sequence),
        "sequenceFingerprint": sha256(sequence.encode("utf-8")).hexdigest(),
        "requiredFormat": "PDB coordinates for the exact assembled sequence",
        "validation": {
            "identityPercent": 100.0,
            "coveragePercent": 100.0,
            "coordinateAnalysisRequired": True,
        },
        "attachmentStatus": "attached" if isinstance(structure_input, dict) else "missing",
        "backendInstanceId": _BACKEND_INSTANCE_ID,
        "sessionStore": "in-memory",
        "workflow": (
            "Submit this exact sequence to a real structure service, download its complete PDB, "
            "then POST it to /api/jobs/{job_id}/structure. Step 11-2 remains paused without a validated model."
        ),
    }


@router.post("/jobs/{job_id}/structure")
def attach_structure_model(job_id: str, attachment: MEVStructureInput) -> dict:
    """Attach an external model for Step 11-2 using transient session input.

    Step 11-2 performs exact sequence/coordinate validation after MEV assembly.
    This endpoint returns metadata only; PDB text is not copied into the
    public Job or step result.
    """
    job = repo.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    if job.status == "completed":
        raise HTTPException(status_code=409, detail="Cannot attach a model to a completed job")

    if attachment.modelUrl:
        parsed = urlparse(attachment.modelUrl)
        if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
            raise HTTPException(
                status_code=422,
                detail="structure model modelUrl must be an HTTPS URL without embedded credentials.",
            )

    # ``coordinateText``/``modelText`` are excluded from public model dumps,
    # but must remain available in the transient runner session for exact PDB
    # sequence validation. They are never copied into the Job record.
    raw = attachment.model_dump(mode="json", exclude_none=True)
    coordinate_text = getattr(attachment, "coordinateText", None) or getattr(attachment, "modelText", None)
    raw.pop("modelText", None)
    if coordinate_text:
        raw["coordinateText"] = coordinate_text
    session = get_session(job.id)
    session["mev_structure_input"] = raw
    session["structure_attachment_backend_instance_id"] = _BACKEND_INSTANCE_ID
    session["structure_attachment_at"] = datetime.now(timezone.utc).isoformat()
    return {
        "status": "attached",
        "source": attachment.source,
        "provider": attachment.provider,
        "method": attachment.method,
        "modelUrl": attachment.modelUrl,
        "attachment": {
            key: value
            for key in ("attachmentId", "fileName", "contentType")
            if (value := getattr(attachment, key, None)) is not None
        },
        "coordinateDataAvailable": bool(coordinate_text),
        "syntheticValues": False,
        "backendInstanceId": _BACKEND_INSTANCE_ID,
        "sessionStore": "in-memory",
    }


_MEV_COMPARISON_METRICS = {
    "mev_length": ("mev_length", "length"),
    "ctl_epitopes": ("ctl_epitopes",),
    "htl_epitopes": ("htl_epitopes",),
    "bcell_epitopes": ("bcell_epitopes",),
}


def _mev_comparison_data(job: Job) -> tuple[dict, dict]:
    """Extract MEV values without presenting unprovenanced data as validated."""
    mev_step = next((s for phase in job.phases for s in phase.steps if s.id == "9-2"), None)
    mev = mev_step.result if mev_step and isinstance(mev_step.result, dict) else {}
    provenance = mev.get("provenance")
    if not isinstance(provenance, dict) or not provenance.get("status"):
        method = " ".join(str(mev.get(key, "")) for key in ("method", "source", "sourceType")).lower()
        status = "local-analysis" if any(token in method for token in ("local", "heuristic", "fallback")) else "unavailable"
        provenance = {
            "status": status,
            "reason": "MEV result has no validated external provenance" if status == "unavailable" else None,
        }

    # Unavailable values are deliberately withheld. Local-analysis values may be
    # compared, but the explicit provenance lets the UI label them honestly.
    if provenance.get("status") == "unavailable":
        return {}, provenance
    metrics = {
        output_key: next((mev[input_key] for input_key in input_keys if mev.get(input_key) is not None), None)
        for output_key, input_keys in _MEV_COMPARISON_METRICS.items()
    }
    coverage_step = next((s for phase in job.phases for s in phase.steps if s.id == "8-1"), None)
    coverage = coverage_step.result.get("coverage") if coverage_step and isinstance(coverage_step.result, dict) else None
    if isinstance(coverage, (int, float)):
        metrics["population_coverage"] = coverage
    return {key: value for key, value in metrics.items() if value is not None}, provenance


@router.get("/jobs/compare")
def compare_jobs(ids: str) -> dict:
    """Return aligned, real funnel/MEV values for an additive multi-run comparison."""
    job_ids = list(dict.fromkeys(item.strip() for item in ids.split(",") if item.strip()))
    if len(job_ids) < 2:
        raise HTTPException(status_code=422, detail="Select at least two job ids to compare")

    selected = []
    for job_id in job_ids:
        job = repo.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
        if job.status != "completed":
            raise HTTPException(status_code=409, detail=f"Job {job_id} is not completed")
        selected.append(job)

    stages = []
    seen_stages = set()
    for job in selected:
        for level in job.funnel or []:
            key = level.get("key")
            if key and key not in seen_stages:
                seen_stages.add(key)
                stages.append({"key": key, "label": level.get("label", key)})

    jobs = []
    for job in selected:
        mev_metrics, provenance = _mev_comparison_data(job)
        jobs.append({
            "id": job.id,
            "name": job.name,
            "status": job.status,
            "funnel": job.funnel or [],
            "mevMetrics": mev_metrics,
            "provenance": provenance,
        })
    return {"funnelStages": stages, "jobs": jobs}


@router.get("/jobs/{job_id}", response_model=Job)
def get_job(job_id: str) -> Job:
    job = repo.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    return _public_job(job)


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
    return _public_job(job)


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
    return _public_job(job)


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
    return _public_job(job)


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
    return _public_job(job)


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
    return _public_job(job)


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