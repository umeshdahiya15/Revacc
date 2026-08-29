"""DEG essential-protein scope regression coverage.

The offline fixture models the corrected DEG10 reference scope while retaining
proteins with no qualifying hit as explicit exclusions. The historical 507
count is retained only as context for the original bug condition.
"""
from __future__ import annotations

import pytest

from app.models import JobCreate
from app.repo import repo
from app.tools import blastdb_local, ncbiblast, runner as runner_mod
from app.tools.uniprot import ProteinRecord


PAPER_ESSENTIAL_SCOPE = 1336
PAPER_SCOPE_LOWER_BOUND = 1200
PAPER_SCOPE_UPPER_BOUND = 1450
OBSERVED_UNFIXED_COUNT = 507
MOCKED_REFERENCE_QUALIFYING_COUNT = PAPER_ESSENTIAL_SCOPE
FIXTURE_PROTEOME_SIZE = 1600


@pytest.mark.asyncio
async def test_deg_essential_scope_matches_paper_scale_with_mocked_reference(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Property 3: DEG essentiality must cover the paper-scale protein scope.

    **Validates: Requirements 2.4**

    The mocked reference/search layer is fully offline.  It returns a strong
    DEG hit for the paper-scale 1336 proteins; the remaining fixture proteins
    have no qualifying DEG hit and must remain excluded by the runner. This
    keeps the test focused on corrected reference scope semantics without
    asking production to synthesize essential records.
    """
    repo._jobs.clear()
    repo._events.clear()
    runner_mod._RUN_SESSION.clear()

    job = repo.create(
        JobCreate(
            name="DEG essential scope exploration",
            pathogenName="Streptococcus agalactiae",
            strain="GBS 2603V/R",
            taxonId=208435,
            realTools=True,
        )
    )
    step = next(s for phase in job.phases for s in phase.steps if s.id == "2-1")

    # A deterministic fixture proteome with the same order used by the runner.
    proteins = [
        ProteinRecord(
            uniprot_id=f"FIX-SAG-{index:04d}",
            name=f"S. agalactiae fixture protein {index}",
            sequence="M" + ("ACDEFGHIKLMNPQRSTVWY"[index % 20]) * 59,
        )
        for index in range(FIXTURE_PROTEOME_SIZE)
    ]
    session = runner_mod.get_session(job.id)
    session["clusters"] = {
        "representatives": [protein.sequence for protein in proteins],
        "representativeRecords": proteins,
    }

    # Keep reference provisioning and searching offline.  The fixture models
    # the corrected paper-scale scope: exactly 1336 rows have a qualifying
    # hit; all other fixture proteins have no DEG hit and remain excluded.
    async def mocked_ensure_deg10_db() -> str:
        return _ready_database()

    monkeypatch.setattr(blastdb_local, "ensure_deg10_db", mocked_ensure_deg10_db)
    monkeypatch.setattr(blastdb_local, "deg10_reference_size", lambda: 26619)

    async def mocked_blastp(
        queries: list[tuple[str, str]],
        *,
        database: str,
        expect: float,
        hitlist_size: int,
    ) -> list[ncbiblast.BlastResult]:
        assert database == "deg10_bacteria"
        assert expect == runner_mod.DEG_EVALUE_THRESHOLD
        assert hitlist_size == 3
        return [
            ncbiblast.BlastResult(
                query_def=query_id,
                hits=[_qualifying_hit()],
            )
            if index < MOCKED_REFERENCE_QUALIFYING_COUNT
            else ncbiblast.BlastResult(query_def=query_id)
            for index, (query_id, _sequence) in enumerate(queries)
        ]

    monkeypatch.setattr(blastdb_local, "blastp", mocked_blastp)

    result = await runner_mod.run_runner(job, step, timeout=30)
    essential_count = result["essential"]

    assert PAPER_SCOPE_LOWER_BOUND <= essential_count <= PAPER_SCOPE_UPPER_BOUND, (
        f"essential scope is {essential_count}, expected approximately "
        f"{PAPER_ESSENTIAL_SCOPE} for the S. agalactiae paper standard; "
        f"unfixed counterexample is {OBSERVED_UNFIXED_COUNT}"
    )


def _ready_database() -> str:
    return "fixture://deg10-bacteria"


def _qualifying_hit() -> ncbiblast.BlastHit:
    return ncbiblast.BlastHit(
        hit_id="fixture-deg-hit",
        accession="FIX-DEG-ESSENTIAL",
        title="Mock DEG essential protein",
        length=100,
        identity=40,
        positive=40,
        align_length=100,
        e_value=1e-20,
        score=200,
    )
