"""E2E driver: run the real pipeline engine to completion for one job.

Drives the actual asyncio Engine (same code the FastAPI lifespan uses) so the
run exercises the real tool runners + wire protocol, and logs every step.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import threading
from datetime import datetime, timezone

sys.path.insert(0, ".")

from app.models import JobCreate  # noqa: E402
from app.repo import repo  # noqa: E402
import app.tools.runner as runner_mod  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("e2e")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%H:%M:%S")


async def drive(taxon: int, name: str, reviewed_only: bool = True) -> None:
    repo._jobs.clear()
    repo._events.clear()
    repo._lock = threading.RLock()
    runner_mod._RUN_SESSION.clear()

    pathogen = "Streptococcus agalactiae" if taxon == 208435 else "Streptococcus pneumoniae"
    job = repo.create(
        JobCreate(
            name=name,
            pathogenName=pathogen,
            taxonId=taxon,
            realTools=True,
            reviewedOnly=reviewed_only,
        )
    )
    log.info("job %s created (taxonId=%s) — driving engine", job.id, taxon)

    from app.simulator import engine

    job.status = "running"
    job = await engine.reconcile_on_start(job)

    steps_seen = 0
    while True:
        current = repo.get(job.id)
        if current is None:
            log.error("job vanished")
            return
        if current.status == "completed":
            log.info("PIPELINE COMPLETED after %s tool steps", steps_seen)
            break
        if current.status == "paused":
            for p in current.phases:
                for s in p.steps:
                    if s.status in ("paused", "failed"):
                        log.info("  BLOCKED %d-%d %s [%s]: %s", p.number, s.number, s.name, s.tool, (s.error.message if s.error else "?"))
            log.info("PIPELINE PAUSED")
            break

        before = {f"{p.number}-{s.number}" for p in current.phases for s in p.steps if s.status == "success"}
        await engine._advance(current)
        after = repo.get(job.id)
        after_set = {f"{p.number}-{s.number}" for p in after.phases for s in p.steps if s.status == "success"}

        # report any step that newly completed in this advance
        newly = after_set - before
        if newly:
            steps_seen += len(newly)
            for p in after.phases:
                for s in p.steps:
                    if f"{p.number}-{s.number}" in newly and s.result:
                        summary = s.result.get("message") or str(s.result)[:140]
                        log.info("  %s %d-%d %s done: %s", _now(), p.number, s.number, s.name, summary)
        elif after.status == "running":
            await asyncio.sleep(0.05)

    final = repo.get(job.id)
    if final:
        completed = sum(1 for p in final.phases for s in p.steps if s.status == "success")
        total = final.totalSteps
        paused = sum(1 for p in final.phases for s in p.steps if s.status in ("paused", "failed"))
        log.info("FINAL: %d/%d steps success, %d paused, job status=%s", completed, total, paused, final.status)
        for p in final.phases:
            bad = [s for s in p.steps if s.status in ("paused", "failed")]
            for s in bad:
                log.info("  FAIL/PAN  %d-%d %s [%s]: %s", p.number, s.number, s.name, s.tool, (s.error.message if s.error else "?"))
        os._exit(0 if (completed == total and final.status == "completed") else 2)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--taxon", type=int, default=1313)
    ap.add_argument("--name", default="e2e-streptococcus-pneumoniae")
    ap.add_argument(
        "--complete-proteome",
        action="store_true",
        help="include reviewed and unreviewed UniProt entries",
    )
    args = ap.parse_args()
    asyncio.run(drive(args.taxon, args.name, reviewed_only=not args.complete_proteome))


if __name__ == "__main__":
    main()
