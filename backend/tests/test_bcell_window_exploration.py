"""Bug-condition exploration for the inert B-cell smoothing window.

This test intentionally fails against the unfixed implementation: run_7_1
ignores JobConfigModel.bCellWindow and uses bcell_local's default window 7.
"""
from __future__ import annotations

import pytest

from app.models import JobCreate
from app.repo import repo
from app.tools import bcell_local, runner as runner_mod, runner_additions


@pytest.mark.asyncio
async def test_run_7_1_uses_configured_bcell_window_for_smoothing() -> None:
    """Property 5: a configured window must govern B-cell smoothing.

    **Validates: Requirements 2.7**

    The sequence and all computation are deterministic and local.  The
    expected configured-window prediction is compared with the legacy
    effective-window prediction so the unfixed call site produces a concrete
    counterexample: configured 16 still yields the window-7 result.
    """
    repo._jobs.clear()
    repo._events.clear()
    runner_mod._RUN_SESSION.clear()

    sequence = "M" + "AERKILV" * 20
    job = repo.create(
        JobCreate(
            name="B-cell window exploration",
            pathogenName="Streptococcus agalactiae",
            bCellWindow=16,
            realTools=True,
        )
    )
    step = next(s for phase in job.phases for s in phase.steps if s.id == "7-1")
    session = runner_mod.get_session(job.id)
    session["vaccine_targets"] = {
        "candidates": [{
            "index": 0,
            "uniprotId": "FIX-BCELL-001",
            "name": "Deterministic B-cell fixture",
            "sequence": sequence,
        }]
    }

    legacy_window_7 = bcell_local.predict_bepipred_epitope(sequence, window_size=7)
    expected_window_16 = bcell_local.predict_bepipred_epitope(sequence, window_size=16)
    await runner_additions.run_7_1(session, job, step)
    observed = session["abc_predictions"]["targets"][0]["abcpred_prediction"]

    # The threshold is part of the preserved BepiPred semantics.
    assert observed["threshold"] == expected_window_16["threshold"] == 0.5
    assert observed == expected_window_16, (
        "configured bCellWindow=16 was inert: "
        f"observed window={observed['window_size']}, "
        f"legacy_equal={observed == legacy_window_7}, "
        f"observed score={observed['epitope_score']}, "
        f"expected window-16 score={expected_window_16['epitope_score']}"
    )
    assert observed != legacy_window_7, (
        "configured window 16 must change the smoothed prediction; "
        "unfixed counterexample is output(window=16) == output(window=7)"
    )
