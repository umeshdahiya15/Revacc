"""Celery tasks for pipeline step execution.

Each task wraps a single pipeline step runner, mirroring the simulator's
`_run_tool` method but blocking on the tool I/O (real network calls) inside
a Celery task context.
"""
from __future__ import annotations

import logging

from .repo import repo
from .tools.runner import STEP_RUNNERS, prune_session, runner_timeout, run_runner, get_session, clear_session

logger = logging.getLogger("mev.worker")


def celery_app_shared_task(*args, **kwargs):
    """Import celery app without circular import issues."""
    from .worker import celery_app
    return celery_app.task(*args, **kwargs)


@celery_app_shared_task
def run_pipeline_step(job_id: str, step_id: str, timeout: float = 120.0) -> dict:
    """Execute a single pipeline step as a Celery task.

    Loads the job from the repo, finds the matching step, runs the tool
    runner, and persists the result.
    """
    from .simulator import engine

    job = repo.get(job_id)
    if job is None:
        logger.error("Job %s not found for step %s", job_id, step_id)
        return {"error": "job not found"}

    step = None
    for phase in job.phases:
        for s in phase.steps:
            if s.id == step_id:
                step = s
                break
    if step is None:
        logger.error("Step %s not found in job %s", step_id, job_id)
        return {"error": "step not found"}

    runner = STEP_RUNNERS.get(step_id)
    if runner is None:
        logger.warning("No runner registered for step %s", step_id)
        step.status = "skipped"
        repo.upsert(job)
        return {"status": "skipped", "reason": "no runner"}

    try:
        get_session(job_id)
        result = run_runner(job, step, timeout=runner_timeout(step_id))
        step.status = "success"
        step.percent = 100
        step.duration = int(result.get("duration") or result.get("elapsed_sec") or 1) or 1
        step.result = result
        repo.upsert(job)

        # Emit events via the simulator (shared logic for both paths)
        import asyncio
        asyncio.run(engine._emit(
            job_id,
            "step_completed",
            phase_no=step.phase,
            step=step.number,
            tool=step.tool,
            duration=step.duration,
            summary=step.result,
        ))

        if step.id == "2-1":
            prune_session(job_id, keep=(
                "essential", "epitopes", "surface_exposed",
                "virulence_factors", "vaccine_targets", "conserved_epitopes",
                "protein_properties", "_funnel_counts",
            ))

        return {"status": "success", "result": result}

    except Exception as exc:
        from .tools.graceful_pause import ToolUnavailableError
        if isinstance(exc, ToolUnavailableError):
            step.status = "paused"
            step.error = {"message": str(exc), "severity": "pause", "tool": exc.tool_name}
            repo.upsert(job)
            return {"status": "paused", "error": str(exc)}
        else:
            step.status = "failed"
            step.error = {"message": str(exc), "severity": "error"}
            repo.upsert(job)
            return {"status": "failed", "error": str(exc)}
    finally:
        clear_session(job_id)
