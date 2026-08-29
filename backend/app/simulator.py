"""Background step-engine.

While a job is `running`, an asyncio task advances it step-by-step,
writing progress into the repo and broadcasting canonical `PipelineEvent`s
on the WebSocket hub — the exact shapes the frontend `usePipelineWebSocket`
hook consumes.

A real deployment replaces `Engine` with a Celery `pipeline_chain` that
blocks on tool I/O and yields events between tools; the wire protocol is
identical, so the frontend will not need to change.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from typing import Optional

from .models import Epitope, Job, PipelineEvent, Step, StepError
from .repo import repo
from .tools import deg
from .tools.graceful_pause import ToolUnavailableError
from .tools.runner import STEP_RUNNERS, prune_session, runner_timeout, run_runner, get_session_peek
from .ws import manager

TICK_MS = 120

_AVG_STEP_SEC = 9  # estimated real-world seconds per tool invocations


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _result_provenance(result: dict, step: Step) -> dict:
    """Attach a machine-readable provenance state to every tool result.

    Results without an explicit real or local method are marked unavailable;
    they are never silently treated as scientific output merely because a
    runner returned a dictionary.
    """
    existing = result.get("provenance")
    if isinstance(existing, dict):
        # Preserve detailed tool provenance (URL, query, release, counts, and
        # cache state). Older results omitted status and were being replaced by
        # a generic object, which rendered fresh UniProt output as unknown.
        status = existing.get("status") or existing.get("cacheType") or existing.get("sourceType")
        if not status:
            status = result.get("sourceType") or result.get("cacheType")
        if not status:
            method = " ".join(str(existing.get(key, result.get(key, ""))) for key in ("method", "source", "tool")).lower()
            if result.get("_paused") or result.get("status") in {"unavailable", "paused", "no_data"}:
                status = "unavailable"
            elif any(token in method for token in ("local", "heuristic", "fallback", "user-provided")):
                status = "local-analysis"
            elif any(token in method for token in ("iedb", "uniprot", "blast", "vfdb", "phobius", "alphafold", "jcat")):
                status = "real"
            else:
                status = "unavailable"
        existing["status"] = status
        existing.setdefault("tool", step.tool)
        existing.setdefault("step", step.id)
        existing.setdefault("method", result.get("method") or result.get("algorithm"))
        return existing
    if result.get("_paused") or result.get("status") in {"unavailable", "paused", "no_data"}:
        status = "unavailable"
    else:
        method = " ".join(
            str(result.get(key, ""))
            for key in ("method", "methodName", "algorithm", "source", "sourceType")
        ).lower()
        if result.get("cached") or "cached-real" in method:
            status = "cached-real"
        elif any(token in method for token in ("local", "heuristic", "fallback", "user-provided")):
            status = "local-analysis"
        elif any(token in method for token in ("iedb", "uniprot", "blast", "vfdb", "phobius", "alphafold", "jcat")):
            status = "real"
        else:
            status = "unavailable"
    result["provenance"] = {
        "status": status,
        "tool": step.tool,
        "step": step.id,
        "method": result.get("method") or result.get("algorithm"),
    }
    return result["provenance"]


def _event(job_id: str, **kw) -> PipelineEvent:
    return PipelineEvent(id=f"evt-{uuid.uuid4().hex[:12]}", jobId=job_id, timestamp=_now_iso(), **kw)


def _recompute_aggregates(job: Job) -> Job:
    completed = 0
    total = 0
    current_phase = 1
    current_step = 1
    first_incomplete: Optional[tuple[int, int]] = None

    for phase in job.phases:
        for step in phase.steps:
            total += 1
            if step.status == "success":
                completed += 1
            elif first_incomplete is None:
                first_incomplete = (phase.number, step.number)

    if first_incomplete is not None:
        current_phase, current_step = first_incomplete

    elapsed = 0
    try:
        started = datetime.fromisoformat(job.createdAt)
        elapsed = max(0, int((datetime.now(timezone.utc) - started).total_seconds()))
    except ValueError:
        pass

    remaining = total - completed
    return Job.model_validate(
        {
            **job.model_dump(mode="json"),
            "currentPhase": current_phase,
            "currentStep": current_step,
            "stepsCompleted": completed,
            "totalSteps": total,
            "progress": round(completed / total * 100) if total else 0,
            "elapsedSeconds": elapsed,
            "estimatedRemaining": remaining * _AVG_STEP_SEC,
        }
    )


def _sync_phase_status(job: Job) -> Job:
    phases = job.phases
    for idx, phase in enumerate(phases):
        steps = phase.steps
        done = all(s.status in ("success", "skipped") for s in steps)
        if done:
            phase.status = "completed"
            phase.duration = sum(s.duration or 0 for s in steps) or None
        elif any(s.status == "failed" for s in steps):
            phase.status = "failed"
        elif any(s.status == "running" for s in steps):
            phase.status = "running"
        elif any(s.status == "paused" for s in steps):
            phase.status = "paused"
        else:
            # first pending phase is the "current" one
            phase.status = "running" if idx == job.currentPhase - 1 else "pending"
    data = job.model_dump(mode="json")
    data["phases"] = [p.model_dump(mode="json") for p in phases]
    return Job.model_validate({**data, "phases": data["phases"]})


class Engine:
    def __init__(self) -> None:
        self._task: Optional[asyncio.Task] = None
        self._stopping = False

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._stopping = False
            self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        self._stopping = True
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _run(self) -> None:
        await asyncio.sleep(0.2)
        while not self._stopping:
            await self._tick()
            await asyncio.sleep(TICK_MS / 1000)

    async def _tick(self) -> None:
        for job in repo.list():
            if job.status != "running":
                continue
            await self._advance(job)

    async def _advance(self, job: Job) -> None:
        job = repo.get(job.id)
        if job is None or job.status != "running":
            return

        target = self._next_incomplete(job)
        if target is None:
            await self._complete(job)
            return

        phase_no, step = target

        if step.status == "pending":
            await self._emit(job.id, "step_started", phase_no, step.number, step.tool, percent=5)

            # Real tool integration: run the registered tool, or pause
            # gracefully if it fails (the frontend surfaces the error banner).
            if STEP_RUNNERS.get(step.id) and job.config.realTools:
                # Mark the step running up-front so the live UI reflects the
                # in-flight tool call for its entire (possibly long) duration.
                step.status = "running"
                step.percent = 5
                step.startedAt = _now_iso()
                await self._refresh(job)
                await self._run_tool(job, phase_no, step)
                return

            step.status = "running"
            step.percent = 5
            step.startedAt = _now_iso()
            await self._refresh(job)
            return

        if step.status == "running":
            # Real-tool steps re-execute their runner instead of following the
            # simulated percent ramp (e.g. after a resume mid-tool-call).
            if STEP_RUNNERS.get(step.id) and job.config.realTools:
                await self._run_tool(job, phase_no, step)
                return
            step.percent = min(92, (step.percent or 0) + 14 + (phase_no % 3))
            await self._emit(job.id, "step_progress", phase_no, step.number, step.tool, percent=step.percent)
            if step.percent >= 90:
                step.status = "success"
                step.percent = 100
                step.duration = 2 + (phase_no % 4) + (step.number % 3)
                step.completedAt = _now_iso()
                await self._emit(
                    job.id,
                    "step_completed",
                    phase_no,
                    step.number,
                    step.tool,
                    duration=step.duration,
                    summary={"tool": step.tool},
                )
            await self._refresh(job)
            return

    async def _refresh(self, job: Job) -> None:
        self._persist(job)

    def _persist(self, job: Job, *, status: Optional[str] = None) -> Job:
        """Merge engine step mutations into the freshest repo copy.

        Between ticks a user may pause/resume the job through the API; that
        lifecycle change lives on the repo object while the engine holds a
        stale snapshot. Always rebase onto the repo copy (preserving its
        status) so a pause issued mid-tool-call is never clobbered, unless
        the engine itself sets an explicit status (e.g. paused on failure,
        completed at the end).
        """
        latest = repo.get(job.id)
        if latest is None:
            latest = job
        latest.phases = job.phases
        latest.epitopes = job.epitopes
        latest.funnel = job.funnel
        if status is not None:
            latest.status = status
        result = _sync_phase_status(_recompute_aggregates(latest))
        repo.upsert(result)
        return result

    async def _run_tool(self, job: Job, phase_no: int, step: Step) -> None:
        """Execute a registered tool runner for a pending step.

        On success the step is marked successful with a real `result`. On
        failure the step is marked failed and the pipeline pauses gracefully,
        matching the Architecture.md "graceful pause on API failure" rule.
        A `ToolUnavailableError` (external tool with no API) pauses the step
        itself, surfacing the "run manually / bypass" banner.
        """
        try:
            result = await run_runner(job, step, timeout=runner_timeout(step.id))
            provenance = _result_provenance(result, step)
            if provenance.get("status") == "unavailable":
                raise ToolUnavailableError(
                    tool_name=step.tool,
                    reason="No validated real or explicitly documented local analysis result was produced.",
                    workaround="Provide the required input/tool and retry the step.",
                )
            step.status = "success"
            step.percent = 100
            step.duration = int(result.get("duration", 1)) or 1
            step.completedAt = _now_iso()
            step.result = {k: v for k, v in result.items() if k != "duration"}
            await self._emit(
                job.id,
                "step_completed",
                phase_no,
                step.number,
                step.tool,
                duration=step.duration,
                summary=step.result,
            )

            if phase_no == 1 or step.id in ("2-1", "2-4", "3-3", "3-4", "8-1", "5-1", "6-1", "10-1"):
                await self._update_funnel(job, step)

            # Mirror real epitope predictions into the job record so they are
            # queryable via GET /api/jobs/{id}/epitopes.
            if step.id in ("5-1", "6-1"):
                session = get_session_peek(job.id) or {}
                job.epitopes = [Epitope(**e) for e in session.get("epitopes") or []]

            self._persist(job)

            # After Phase 2-1 the big per-job payload (proteome + cluster
            # FASTA) can be released; the essential candidates + later filter
            # stages needed by Phases 3/5-6 are kept in the run session.
            if step.id == "2-1":
                prune_session(
                    job.id,
                    keep=(
                        "essential",
                        "epitopes",
                        "surface_exposed",
                        "virulence_factors",
                        "vaccine_targets",
                        "conserved_epitopes",
                        "protein_properties",
                        "_funnel_counts",
                    ),
                )
        except ToolUnavailableError as exc:
            step.status = "paused"
            step.error = StepError(
                message=str(exc),
                severity="pause",
                tool=exc.tool_name,
                lastFailedAt=_now_iso(),
            )
            step.result = {
                "_paused": True,
                "tool": exc.tool_name,
                "reason": exc.reason,
                "workaround": exc.workaround,
                "message": str(exc),
            }
            # Official lifecycle status is supplied only by its backend-safe
            # adapter. Existing pauses retain their exact public shape.
            if exc.public_status is not None:
                step.result["officialLifecycle"] = exc.public_status
            job.status = "paused"
            await self._emit(
                job.id,
                "step_failed",
                phase_no,
                step.number,
                step.tool,
                error=str(exc),
            )
            await self._emit(
                job.id,
                "pipeline_paused",
                message=f"External tool required — {exc.tool_name}: run manually or bypass",
            )
            self._persist(job, status="paused")
        except Exception as exc:  # noqa: BLE001 - surface any tool failure
            step.status = "failed"
            step.error = StepError(
                message=str(exc) or exc.__class__.__name__,
                retries=3,
                lastFailedAt=_now_iso(),
            )
            job.status = "paused"
            await self._emit(
                job.id,
                "step_failed",
                phase_no,
                step.number,
                step.tool,
                error=str(exc) or exc.__class__.__name__,
            )
            await self._emit(job.id, "pipeline_paused", message="Step failed — review and retry")
            self._persist(job, status="paused")

    async def _update_funnel(self, job: Job, step: Step) -> None:
        """Push the real Phase 1/2/5/6 counts into the job funnel so the
        frontend FilterFunnel chart reflects actual numbers."""
        session = get_session_peek(job.id) or {}
        # _funnel_counts is stashed by each runner and survives session pruning.
        fc = session.get("_funnel_counts") or {}
        previous = {lvl["key"]: lvl["count"] for lvl in (job.funnel or [])}

        # Proteins: live count → stashed → previous funnel
        proteins = fc.get("proteins") or len(session.get("proteins") or []) or previous.get("proteins") or 0

        # Redundant: stashed → live → previous
        # Show total CD-HIT clusters (the number after redundancy removal),
        # which matches the "Non-redundant" count in MEV papers.
        redundant = fc.get("redundant") or previous.get("redundant") or 0
        if not redundant:
            clusters = session.get("clusters") or {}
            if clusters.get("indices"):
                redundant = len(clusters["indices"])

        # Essential: live → stashed → previous
        essential = session.get("essential") or {}
        if essential.get("indices"):
            essential_count = len(essential["indices"])
        else:
            essential_count = fc.get("essential") or previous.get("essential") or 0

        epitopes = session.get("epitopes") or []

        funnel = [
            {
                "key": "proteins",
                "label": "Proteins curated",
                "count": proteins,
                "filterLabel": f"UniProt txid {job.config.taxonId}",
            },
            {
                "key": "redundant",
                "label": "Non-redundant clusters",
                "count": redundant,
                "filterLabel": f"CD-HIT ≥ {job.config.cdHitThreshold}% identity",
            },
]
        # Essentiality is now always determined against the complete DEG 10
        # bacterial essential-gene set (see runner.runner_identify_essential).
        deg_label = "DEG 10 (complete bacterial set)"
        if essential_count:
                funnel.append(
                    {
                        "key": "essential",
                        "label": "Essential proteins",
                        "count": essential_count,
                        "filterLabel": deg_label,
                    }
                )

        # Phase 2-4 (Phobius) / 3-3 (VFDB) / 3-4 (human homology) filter stages.
        # Each only appears once its runner has written real counts into the
        # session; otherwise the stashed or previously-reported value is preserved.
        # Report the PSORTb subcellular-localization count (paper's
        # "Proteins Selected through Subcellular localization" row). The
        # working set is further refined by the TMH/Phobius steps downstream.
        surface_exposed = session.get("surface_exposed") or {}
        se_count = fc.get("surface_exposed") or surface_exposed.get("count") or previous.get("surface_exposed")
        if se_count:
            funnel.append(
                {
                    "key": "surface_exposed",
                    "label": "Surface-exposed",
                    "count": se_count,
                    "filterLabel": "PSORTb 3.0",
                }
            )

        virulence = session.get("virulence_factors") or {}
        vf_count = virulence.get("count") or fc.get("virulence_factors") or previous.get("virulence_factors")
        if vf_count:
            funnel.append(
                {
                    "key": "virulence_factors",
                    "label": "Virulence factors",
                    "count": vf_count,
                    "filterLabel": "BLASTp + VFDB",
                }
            )

        targets = session.get("vaccine_targets") or {}
        vt_count = targets.get("count") or fc.get("targets") or previous.get("targets")
        if vt_count:
            funnel.append(
                {
                    "key": "targets",
                    "label": "Vaccine candidates",
                    "count": vt_count,
                    "filterLabel": "Human homology",
                }
            )

        ctl = [e for e in epitopes if e["type"] == "CTL"]
        htl = [e for e in epitopes if e["type"] == "HTL"]
        if ctl:
            funnel.append(
                {
                    "key": "mhc-i",
                    "label": "MHC class I epitopes",
                    "count": len(ctl),
                    "filterLabel": "IEDB NetMHCpan EL",
                    "final": not htl,
                }
            )
        # Do not display projected or synthetic epitope counts. The funnel is
        # intentionally silent until the real IEDB runner writes predictions.
        if htl:
            funnel.append(
                {
                    "key": "mhc-ii",
                    "label": "MHC class II epitopes",
                    "count": len(htl),
                    "filterLabel": "IEDB NetMHCIIpan EL",
                    "final": True,
                }
            )
        job.funnel = funnel
        validation = _mev_validation_data(session)
        session["mev_validation"] = validation
        await self._emit(job.id, "filter_applied", phase=step.phase, step=step.number, summary={"funnel": funnel, "validation": validation})

    def _next_incomplete(self, job: Job) -> Optional[tuple[int, object]]:
        for phase in job.phases:
            for step in phase.steps:
                if step.status in ("pending", "running"):
                    return phase.number, step
        return None

    async def _complete(self, job: Job) -> None:
        final = self._persist(job, status="completed")
        repo.upsert(
            Job.model_validate(
                {**final.model_dump(mode="json"), "progress": 100, "estimatedRemaining": 0}
            )
        )
        await self._emit(job.id, "pipeline_completed", message="All 14 phases finished")

    async def _emit(self, job_id: str, kind: str, phase=None, step=None, tool=None, **extra) -> None:
        event = _event(job_id, type=kind, phase=phase, step=step, tool=tool, **extra)
        repo.push_event(event)
        await manager.broadcast(job_id, event.model_dump(mode="json"))

    async def reconcile_on_start(self, job: Job) -> Job:
        """Reset any earlier progress bookmark so the engine continues in place.

        Steps that failed while executing a registered real-tool runner are
        reset to pending so a resume/retry actually re-runs the tool instead
        of being skipped to false completion.
        """
        retried = False
        for phase in job.phases:
            for step in phase.steps:
                if step.status in ("failed", "paused") and STEP_RUNNERS.get(step.id) and job.config.realTools:
                    step.status = "pending"
                    step.percent = None
                    step.error = None
                    step.result = None
                    step.completedAt = None
                    retried = True
        if retried:
            job.status = "running"
        job = _recompute_aggregates(job)
        job = _sync_phase_status(job)
        return repo.upsert(job)


def _mev_validation_data(session: dict) -> dict:
    """Collect per-gate pass/fail results into a validation summary."""
    validation = {}

    # 10-1 ProtParam: instability stable, GRAVY, etc.
    phys = session.get("physicochemical") or {}
    validation["antigenicity"] = {
        "instability_index": phys.get("instability_index"),
        "instability_stable": phys.get("instability_stable"),
        "gravy": phys.get("gravy"),
    }

    # 10-2 VaxiJen: overall antigenicity
    mev_antigenicity = session.get("mev_antigenicity") or {}
    validation["overall_antigenicity"] = {
        "score": mev_antigenicity.get("score"),
        "method": mev_antigenicity.get("method"),
    }

    # 10-3 AlgPred: already incorporated into epitopes; skip separate entry
    # (or mark as completed if CTL/HTL allergenicity was scored)

    # 10-4 ToxinPred: toxin calls stored on epitopes
    ctl_epitopes = session.get("epitopes") or []
    ctl_toxic = sum(1 for e in ctl_epitopes if e.get("type") == "CTL" and e.get("isToxic"))
    htl_toxic = sum(1 for e in ctl_epitopes if e.get("type") == "HTL" and e.get("isToxic"))
    validation["toxicity"] = {
        "ctl_toxic_count": ctl_toxic,
        "htl_toxic_count": htl_toxic,
    }

    # 10-5 Protein-Sol: solubility
    meas = session.get("mev_solubility") or {}
    validation["solubility"] = {
        "solubility_score": meas.get("solubility_score"),
        "is_soluble": meas.get("is_soluble"),
    }

    # Overall status: all gates pass if every sub-gate is present and non-failing
    all_pass = (
        validation.get("antigenicity", {}).get("instability_stable") is not None
        and validation.get("overall_antigenicity", {}).get("score") is not None
        and validation.get("toxicity", {}).get("ctl_toxic_count", 0) is not None
        and validation.get("solubility", {}).get("is_soluble") is not None
    )
    validation["overall_status"] = "pass" if all_pass else "fail"

    return validation


engine = Engine()
