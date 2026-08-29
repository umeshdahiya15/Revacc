"""Bug-condition exploration for the under-detected surface-protein scope.

This test intentionally fails against the unfixed implementation.  Step 2-2
recognizes the PSORTb surface categories, but step 2-4 currently overwrites
that broader set with Phobius-only signal-peptide/TM classifications.  The
fixture is deterministic and entirely offline.
"""
from __future__ import annotations

from typing import Any

import pytest

from app.models import JobCreate
from app.repo import repo
from app.tools import runner as runner_mod
from app.tools import runner_additions


PAPER_SURFACE_SCOPE = 408
SURFACE_SOURCE_IDS = (
    "FIX-OM-001",
    "FIX-LIPO-001",
    "FIX-WALL-001",
    "FIX-SECRETED-001",
)


@pytest.mark.asyncio
async def test_surface_localization_keeps_psortb_surface_categories(
    fixture_proteome: list[dict[str, Any]],
    mock_ebi_psortb: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Property 4: surface localization must cover the paper-scale scope.

    **Validates: Requirements 2.5, 2.6**

    PSORTb is mocked to return 408 surface proteins (102 each from the
    outer-membrane, lipoprotein, cell-wall, and extracellular categories) and
    one intracellular control.  Phobius is mocked with no signal/TM features,
    reproducing the current Phobius-only refinement path.  Correct behavior is
    to retain the PSORTb surface categories rather than replace them; the
    unfixed implementation returns zero after step 2-4.
    """
    repo._jobs.clear()
    repo._events.clear()
    runner_mod._RUN_SESSION.clear()

    job = repo.create(
        JobCreate(
            name="Surface localization exploration",
            pathogenName="Streptococcus agalactiae",
            strain="GBS 2603V/R",
            taxonId=208435,
            realTools=True,
        )
    )
    session = runner_mod.get_session(job.id)

    # Expand the shared category fixture to the paper-scale surface count
    # without making any external requests.  Repeated sequences are safe here:
    # the Phobius cache is disabled below and IDs remain distinct.
    category_proteins: list[dict[str, Any]] = []
    for source in fixture_proteome[:3] + fixture_proteome[4:5]:
        for copy_index in range(PAPER_SURFACE_SCOPE // 4):
            category_proteins.append(
                {
                    **source,
                    "index": len(category_proteins),
                    "uniprotId": f"{source['uniprotId']}-{copy_index:03d}",
                }
            )

    intracellular = {
        **fixture_proteome[3],
        "index": len(category_proteins),
        "uniprotId": "FIX-INTRA-CONTROL",
    }
    candidates = category_proteins + [intracellular]
    session["essential"] = {"candidates": candidates, "indices": list(range(len(candidates)))}

    # Keep the localization layers deterministic and offline.  The fixture's
    # PSORTb categories are placed directly into the pre-Phobius surface set,
    # matching the broader set that run_2_2 is expected to provide.  The EBI
    # mock defaults to an empty Phobius feature table, i.e. intracellular.
    session["surface_exposed"] = {
        "candidates": candidates,
        "count": len(category_proteins),
    }
    monkeypatch.setattr(runner_additions, "PHOBIUS_RATE_LIMIT_SEC", 0.0)
    monkeypatch.setattr(runner_additions, "_load_phobius_cache", lambda: {})
    monkeypatch.setattr(runner_additions, "_save_phobius_cache", lambda _cache: None)

    step_2_4 = next(
        step for phase in job.phases for step in phase.steps if step.id == "2-4"
    )

    phobius_result = await runner_additions.run_2_4(session, job, step_2_4)
    final_candidates = (session.get("surface_exposed") or {}).get("candidates") or []
    final_ids = {candidate["uniprotId"] for candidate in final_candidates}

    expected_category_ids = {
        candidate["uniprotId"]
        for candidate in category_proteins
        if candidate["uniprotId"].startswith(SURFACE_SOURCE_IDS)
    }
    missed_ids = expected_category_ids - final_ids

    assert phobius_result["surface_exposed_count"] >= PAPER_SURFACE_SCOPE * 0.9, (
        f"surface count was {phobius_result['surface_exposed_count']}, expected "
        f"near the paper-scale target {PAPER_SURFACE_SCOPE}; "
        f"unfixed Phobius-only counterexample missed {len(missed_ids)} "
        f"surface IDs (sample: {sorted(missed_ids)[:8]})"
    )
    assert not missed_ids, (
        f"Phobius-only refinement dropped {len(missed_ids)} PSORTb surface IDs; "
        f"expected {PAPER_SURFACE_SCOPE} surface proteins, observed "
        f"{phobius_result['surface_exposed_count']}"
    )
    assert intracellular["uniprotId"] not in final_ids
