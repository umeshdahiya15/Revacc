"""IEDB outage policy tests.

An exhausted IEDB request must remain scientifically honest: without a live or
cached-real response, the step pauses and emits no synthetic epitope rows.
"""
from __future__ import annotations

import pytest

from app.models import JobCreate
from app.tools.graceful_pause import ToolUnavailableError
from app.repo import repo
from app.tools import runner as runner_mod


@pytest.mark.asyncio
@pytest.mark.parametrize(("step_id", "epitope_type"), [("5-1", "CTL"), ("6-1", "HTL")])
async def test_rate_limited_iedb_step_completes_with_fallback_epitopes(
    step_id: str,
    epitope_type: str,
    mock_iedb_post,
) -> None:
    """An exhausted IEDB burst pauses instead of fabricating scientific rows."""
    repo._jobs.clear()
    repo._events.clear()
    runner_mod._RUN_SESSION.clear()

    job = repo.create(
        JobCreate(
            name="IEDB fallback exploration",
            pathogenName="Streptococcus agalactiae",
            taxonId=208435,
            hlaMhc1=["HLA-A*02:01"],
            hlaMhc2=["HLA-DRB1*01:01"],
            realTools=True,
        )
    )
    runner_mod.get_session(job.id)["essential"] = {
        "indices": [0],
        "candidates": [
            {
                "index": 0,
                "uniprotId": "FIX-IEDB-001",
                "name": "Exploration protein",
                "sequence": "M" + "A" * 59,
            }
        ],
    }
    mock_iedb_post.error = ConnectionError("IEDB unreachable after 4 attempts")
    step = next(s for phase in job.phases for s in phase.steps if s.id == step_id)

    with pytest.raises(ToolUnavailableError) as caught:
        await runner_mod.run_runner(job, step, timeout=runner_mod.runner_timeout(step_id))

    assert caught.value.tool_name.startswith("IEDB")
    assert "no synthetic epitopes" in str(caught.value).lower()
    assert runner_mod.get_session_peek(job.id).get("epitopes", []) == []
