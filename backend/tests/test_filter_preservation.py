"""Baseline preservation tests for unchanged filters and exclusion gates.

These tests are intentionally offline and exercise the unfixed implementation.
They record the current decisions/counts that later pipeline fixes must not
change for non-bug-condition inputs.
"""
from __future__ import annotations

from difflib import SequenceMatcher
from typing import Any
from unittest.mock import patch

import pytest
from hypothesis import given, strategies as st

from app.models import JobCreate
from app.repo import repo
from app.tools import blastdb_local, cdhit, runner as runner_mod
from app.tools import algpred_local, ncbiblast, runner_additions, vaxijen_local, vfdb
from app.tools.uniprot import ProteinRecord


@pytest.mark.parametrize("changes", [20])
def test_cdhit_preserves_eighty_percent_identity_boundary(changes: int) -> None:
    """CD-HIT keeps the existing inclusive >= 0.80 identity decision.

    **Validates: Requirements 3.3**
    """
    reference = "A" * 100
    variant = "A" * (100 - changes) + "C" * changes

    clusters, representatives = cdhit.cluster([reference, variant], identity=0.80)

    assert SequenceMatcher(None, reference, variant).ratio() >= 0.80
    assert len(clusters) == 1
    assert representatives == [0]
    assert cdhit.stats([reference, variant], clusters, 0.80)["removedAsRedundant"] == 1


@given(changes=st.integers(min_value=0, max_value=30))
def test_cdhit_count_matches_existing_identity_rule(changes: int) -> None:
    """Generated sequence pairs retain the baseline CD-HIT count rule.

    **Validates: Requirements 3.3**
    """
    reference = "A" * 100
    variant = "A" * (100 - changes) + "C" * changes
    expected_cluster_count = 1 if SequenceMatcher(None, reference, variant).ratio() >= 0.80 else 2

    clusters, _ = cdhit.cluster([reference, variant], identity=0.80)

    assert len(clusters) == expected_cluster_count


@given(
    identity=st.sampled_from([29.9, 30.0, 30.1, 45.0]),
    evalue=st.sampled_from([9.9e-5, 1e-4, 1.1e-4]),
    bitscore=st.sampled_from([99.9, 100.0, 100.1]),
)
def test_vfdb_preserves_identity_evalue_and_bitscore_gates(
    identity: float,
    evalue: float,
    bitscore: float,
) -> None:
    """VFDB retains identity >=30%, e-value <=1e-4, and bit score >100 semantics.

    **Validates: Requirements 3.3**
    """
    candidate = {"uniprotId": "FIX-VFDB-001", "sequence": "M" + "A" * 59}
    hit = vfdb.VFDBHit(
        query_id=candidate["uniprotId"],
        subject_id="FIX-VF-REF",
        subject_title="fixture virulence factor",
        identity=identity,
        evalue=evalue,
        align_length=60,
        bitscore=bitscore,
    )
    with patch.object(vfdb, "_have_blast", return_value=True), patch.object(
        vfdb, "ensure_vfdb_fasta", return_value="fixture://vfdb.fasta"
    ), patch.object(vfdb, "_build_db", return_value="fixture://vfdb"), patch.object(
        vfdb, "_run_blastp", return_value=[hit]
    ):
        result = vfdb.blast_vfdb([candidate])

    expected = identity >= 30.0 and evalue <= 1e-4 and bitscore > 100.0
    assert result.virulence_count == int(expected)
    assert result.results[0].is_virulence_factor is expected


def test_unchanged_filter_threshold_constants_are_preserved() -> None:
    """The baseline exposes the documented unchanged threshold values.

    **Validates: Requirements 3.3**
    """
    # DEG identity threshold was updated from 20.0 to 40.0 to match paper standard
    # (Barazesh et al. 2024). See HANDOVER.md for details.
    assert runner_mod.DEG_IDENTITY_THRESHOLD == 40.0
    assert vfdb.VFDB_BITSCORE_THRESHOLD == 100.0
    assert vfdb.VFDB_EVALUE_THRESHOLD == 1e-4
    assert vfdb.VFDB_IDENTITY_THRESHOLD == 30.0
    assert runner_additions.HUMAN_HOMOLOGY_IDENTITY_THRESHOLD == 0.30
    assert vaxijen_local.THRESHOLDS["bacteria"] == 0.50


@given(score=st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False))
def test_vaxijen_preserves_inclusive_half_score_cutoff(
    score: float,
) -> None:
    """VaxiJen keeps bacterial scores >=0.50 antigenic.

    **Validates: Requirements 3.3**
    """
    with patch.object(vaxijen_local, "predict_antigenicity", return_value=score):
        prediction = vaxijen_local.is_antigenic("M" + "A" * 59, organism_type="bacteria")

    assert prediction["threshold"] == 0.50
    assert prediction["is_antigenic"] is (score >= 0.50)


@given(reference_match=st.sampled_from([0.0, 35.0, 36.0, 36.833, 37.0, 50.0]))
def test_algpred_preserves_0321_allergen_cutoff(reference_match: float) -> None:
    """AlgPred keeps its existing 0.321 score boundary.

    **Validates: Requirements 3.3**
    """
    # 60 alanines are in the 5–80 kDa range, contributing the baseline 0.1
    # score/reason.  The identity contribution is reference_match * 0.006
    # only when the existing strict >35% identity gate is met.
    expected_score = (reference_match / 100.0 * 0.6 if reference_match > 35.0 else 0.0) + 0.1

    prediction = algpred_local.predict_allergenicity(
        "A" * 60,
        reference_allergen_match=reference_match,
    )

    assert prediction["allergen_score"] == round(expected_score, 4)
    assert prediction["is_allergen"] is (expected_score >= 0.321)


@pytest.mark.asyncio
async def test_human_homology_preserves_thirty_percent_exclusion_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Human homologs at >=30% identity are excluded, lower hits remain.

    **Validates: Requirements 3.3**
    """
    _reset_state()
    job = repo.create(JobCreate(name="human homology filter preservation", realTools=True))
    step = next(s for phase in job.phases for s in phase.steps if s.id == "3-4")
    candidates = [
        {"index": 0, "uniprotId": "FIX-HUMAN-LOW", "name": "low", "sequence": "M" + "A" * 59},
        {"index": 1, "uniprotId": "FIX-HUMAN-BOUNDARY", "name": "boundary", "sequence": "M" + "C" * 59},
        {"index": 2, "uniprotId": "FIX-HUMAN-HIGH", "name": "high", "sequence": "M" + "G" * 59},
    ]
    runner_mod.get_session(job.id)["virulence_factors"] = {"candidates": candidates}

    async def mocked_blast(
        queries: list[tuple[str, str]], **_kwargs: Any
    ) -> list[ncbiblast.BlastResult]:
        identities = {"FIX-HUMAN-LOW": 29, "FIX-HUMAN-BOUNDARY": 30, "FIX-HUMAN-HIGH": 31}
        return [
            ncbiblast.BlastResult(
                query_def=query_id,
                hits=[ncbiblast.BlastHit(
                    hit_id="fixture-human",
                    accession="FIX-HUMAN-REF",
                    title="fixture human protein",
                    length=100,
                    identity=identities[query_id],
                    positive=identities[query_id],
                    align_length=100,
                    e_value=1e-20,
                    score=200,
                )],
            )
            for query_id, _sequence in queries
        ]

    async def no_op_ensure_human_db() -> None:
        pass

    monkeypatch.setattr(blastdb_local, "blastp", mocked_blast)
    monkeypatch.setattr(blastdb_local, "ensure_human_db", no_op_ensure_human_db)

    result = await runner_additions.run_3_4(runner_mod.get_session(job.id), job, step)

    assert result["homologous_count"] == 2
    assert result["non_homologous_count"] == 1
    assert [c["uniprotId"] for c in runner_mod.get_session(job.id)["vaccine_targets"]["candidates"]] == [
        "FIX-HUMAN-LOW"
    ]


def _reset_state() -> None:
    repo._jobs.clear()
    repo._events.clear()
    runner_mod._RUN_SESSION.clear()


@pytest.mark.asyncio
async def test_deg_without_qualifying_hit_stays_out_of_essential_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Proteins lacking a qualifying DEG hit remain excluded from essentials.

    **Validates: Requirements 3.4**
    """
    _reset_state()
    job = repo.create(JobCreate(name="DEG exclusion preservation", realTools=True))
    step = next(s for phase in job.phases for s in phase.steps if s.id == "2-1")
    records = [
        ProteinRecord(f"FIX-DEG-{index}", f"fixture {index}", "M" + aa * 59)
        for index, aa in enumerate(("A", "C", "G"))
    ]
    runner_mod.get_session(job.id)["clusters"] = {
        "representatives": [record.sequence for record in records],
        "representativeRecords": records,
    }

    async def mocked_ensure_db() -> str:
        return "fixture://deg10"

    async def mocked_blast(
        queries: list[tuple[str, str]], **_kwargs: Any
    ) -> list[ncbiblast.BlastResult]:
        # 40% is the current inclusive DEG identity threshold (matching paper standard).
        # The first query qualifies, the second is below it, and the third has no hit.
        return [
            ncbiblast.BlastResult(
                query_def=query_id,
                hits=[] if index == 2 else [ncbiblast.BlastHit(
                    hit_id="fixture-deg",
                    accession="FIX-DEG-REF",
                    title="fixture essential protein",
                    length=100,
                    identity=40 if index == 0 else 39,
                    positive=40 if index == 0 else 39,
                    align_length=100,
                    e_value=1e-20,
                    score=200,
                )],
            )
            for index, (query_id, _sequence) in enumerate(queries)
        ]

    monkeypatch.setattr(blastdb_local, "ensure_deg10_db", mocked_ensure_db)
    monkeypatch.setattr(blastdb_local, "deg10_reference_size", lambda: 26619)
    monkeypatch.setattr(blastdb_local, "blastp", mocked_blast)

    result = await runner_mod.runner_identify_essential(job, step)

    assert result["essential"] == 1
    assert runner_mod.get_session(job.id)["essential"]["indices"] == [0]
    assert [candidate["uniprotId"] for candidate in runner_mod.get_session(job.id)["essential"]["candidates"]] == [
        "FIX-DEG-0"
    ]


@pytest.mark.asyncio
async def test_intracellular_without_signal_or_tm_stays_out_of_surface_set(
    fixture_proteome: list[dict[str, Any]],
    mock_ebi_psortb: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Phobius keeps intracellular proteins out of final surface results.

    **Validates: Requirements 3.5**
    """
    _reset_state()
    job = repo.create(JobCreate(name="surface exclusion preservation", realTools=True))
    step = next(s for phase in job.phases for s in phase.steps if s.id == "2-4")
    intracellular = fixture_proteome[3]
    secreted = fixture_proteome[4]
    candidates = [intracellular, secreted]
    session = runner_mod.get_session(job.id)
    session["surface_exposed"] = {"candidates": candidates, "count": len(candidates)}

    mock_ebi_psortb["phobius_by_sequence"][intracellular["sequence"]] = "ID MOCK\n//"
    mock_ebi_psortb["phobius_by_sequence"][secreted["sequence"]] = "ID MOCK\nFT   SIGNAL      1      24\n//"
    monkeypatch.setattr(runner_additions, "PHOBIUS_RATE_LIMIT_SEC", 0.0)
    monkeypatch.setattr(runner_additions, "_load_phobius_cache", lambda: {})
    monkeypatch.setattr(runner_additions, "_save_phobius_cache", lambda _cache: None)

    result = await runner_additions.run_2_4(session, job, step)
    final_ids = {
        candidate["uniprotId"]
        for candidate in session["surface_exposed"]["candidates"]
    }

    assert result["intracellular_count"] == 1
    assert result["surface_exposed_count"] == 1
    assert secreted["uniprotId"] in final_ids
    assert intracellular["uniprotId"] not in final_ids
