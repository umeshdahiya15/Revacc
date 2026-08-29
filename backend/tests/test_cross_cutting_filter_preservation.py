"""Cross-cutting Property 9 regression test for unchanged filter stages.

The test uses the pre-fix filter contracts as an explicit oracle and runs the
same candidate set through the current fixed stage implementations. External
BLAST services are replaced only at their test seams; no production result is
fabricated or persisted.
"""
from __future__ import annotations

import asyncio
from difflib import SequenceMatcher
from typing import Any

import pytest
from hypothesis import HealthCheck, given, settings, strategies as st

from app.tools import cdhit, ncbiblast, runner_additions, vfdb, vaxijen_local


@st.composite
def _candidate_sets(draw: st.DrawFn) -> list[dict[str, Any]]:
    count = draw(st.integers(min_value=1, max_value=8))
    changes = draw(st.lists(st.integers(0, 30), min_size=count, max_size=count))
    vf_identity = draw(
        st.lists(st.sampled_from([0, 29, 30, 31, 100]), min_size=count, max_size=count)
    )
    vf_evalue = draw(
        st.lists(st.sampled_from([1e-3, 1e-4, 1.1e-4, 1e-5]), min_size=count, max_size=count)
    )
    vf_bitscore = draw(
        st.lists(st.sampled_from([99.0, 100.0, 101.0]), min_size=count, max_size=count)
    )
    human_identity = draw(
        st.lists(st.sampled_from([0, 29, 30, 31, 100]), min_size=count, max_size=count)
    )
    vax_score = draw(
        st.lists(
            st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False),
            min_size=count,
            max_size=count,
        )
    )
    allergen_identity = draw(
        st.lists(st.sampled_from([0.0, 35.0, 36.0, 37.0, 50.0]), min_size=count, max_size=count)
    )
    suffixes = ("AA", "AG", "AM", "GA", "GG", "GM", "MA", "MG")
    return [
        {
            "index": index,
            "uniprotId": f"FIX-FILTER-{index}",
            "name": f"fixture candidate {index}",
            # The two-residue suffix makes sequences unique while retaining a
            # simple, controlled identity relation for the CD-HIT oracle.
            "sequence": "M" + "A" * (97 - change) + "G" * change + suffixes[index],
            "vf_identity": vf_identity[index],
            "vf_evalue": vf_evalue[index],
            "vf_bitscore": vf_bitscore[index],
            "human_identity": human_identity[index],
            "vax_score": vax_score[index],
            "allergen_identity": allergen_identity[index],
            "pfam_domains": [],
        }
        for index, change in enumerate(changes)
    ]


def _legacy_representatives(candidates: list[dict[str, Any]]) -> list[int]:
    """Return representatives under the pre-fix greedy CD-HIT contract."""
    representatives: list[int] = []
    for index, candidate in enumerate(candidates):
        sequence = candidate["sequence"]
        redundant = any(
            min(len(sequence), len(candidates[rep]["sequence"]))
            / max(len(sequence), len(candidates[rep]["sequence"]))
            >= 0.80
            and SequenceMatcher(None, candidates[rep]["sequence"], sequence).ratio() >= 0.80
            for rep in representatives
        )
        if not redundant:
            representatives.append(index)
    return representatives


def _legacy_algpred_keeps(candidate: dict[str, Any]) -> bool:
    """Capture AlgPred's existing no-domain/no-motif decision contract."""
    identity = candidate["allergen_identity"]
    score = (identity / 100.0 * 0.6 if identity > 35.0 else 0.0) + 0.1
    return not (score >= 0.321 and identity > 35.0)


def _expected_snapshot(candidates: list[dict[str, Any]]) -> dict[str, Any]:
    representatives = _legacy_representatives(candidates)
    cd_candidates = [candidates[index] for index in representatives]
    non_allergenic = [c for c in cd_candidates if _legacy_algpred_keeps(c)]
    antigenic = [c for c in cd_candidates if c["vax_score"] >= 0.50]
    virulence = [
        c
        for c in cd_candidates
        if c["vf_identity"] >= 30
        and c["vf_evalue"] <= 1e-4
        and c["vf_bitscore"] >= 100.0
    ]
    vaccine_targets = [c for c in cd_candidates if c["human_identity"] < 30]

    def stage(candidates_for_stage: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "count": len(candidates_for_stage),
            "ids": [c["uniprotId"] for c in candidates_for_stage],
        }

    return {
        "cdhit": {"count": len(cd_candidates), "ids": [c["uniprotId"] for c in cd_candidates]},
        "algpred": stage(non_allergenic),
        "vaxijen": stage(antigenic),
        "vfdb": stage(virulence),
        "human_homology": stage(vaccine_targets),
    }


@pytest.mark.parametrize("identity_threshold", [0.80])
@settings(
    max_examples=30,
    derandomize=True,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(candidates=_candidate_sets())
def test_property9_cross_cutting_filter_preservation(
    candidates: list[dict[str, Any]],
    identity_threshold: float,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unchanged filter decisions/counts remain equal to their legacy contract.

    **Validates: Requirements 3.3**

    The production fixes do not touch these stages.  This property checks the
    complete non-DEG/non-surface filter chain over randomized candidate sets,
    rather than checking only isolated threshold examples.
    """
    expected = _expected_snapshot(candidates)
    clusters, representatives = cdhit.cluster(
        [candidate["sequence"] for candidate in candidates], identity=identity_threshold
    )
    representative_candidates = [candidates[index] for index in representatives]

    score_by_sequence = {
        candidate["sequence"]: candidate["vax_score"] for candidate in candidates
    }

    def predict_antigenicity(sequence: str, _organism_type: str = "bacteria") -> float:
        return score_by_sequence[sequence]

    monkeypatch.setattr(
        vaxijen_local, "predict_antigenicity", predict_antigenicity
    )

    def run_vfdb(query_fasta: str, db_name: str) -> list[vfdb.VFDBHit]:
        del query_fasta, db_name
        return [
            vfdb.VFDBHit(
                query_id=candidate["uniprotId"],
                subject_id="FIX-VF-REF",
                subject_title="fixture virulence factor",
                identity=candidate["vf_identity"],
                evalue=candidate["vf_evalue"],
                align_length=100,
                bitscore=candidate["vf_bitscore"],
            )
            for candidate in candidates
        ]

    monkeypatch.setattr(vfdb, "_have_blast", lambda: True)
    monkeypatch.setattr(vfdb, "ensure_vfdb_fasta", lambda: "fixture://vfdb.fasta")
    monkeypatch.setattr(vfdb, "_build_db", lambda _path, _cache: "fixture://vfdb")
    monkeypatch.setattr(vfdb, "_run_blastp", run_vfdb)

    async def run_human_homology(
        queries: list[tuple[str, str]], **_kwargs: Any
    ) -> tuple[list[ncbiblast.BlastResult], str]:
        by_id = {candidate["uniprotId"]: candidate for candidate in candidates}
        return [
            ncbiblast.BlastResult(
                query_def=query_id,
                hits=[
                    ncbiblast.BlastHit(
                        hit_id="FIX-HUMAN-REF",
                        accession="FIX-HUMAN-REF",
                        title="fixture human protein",
                        length=100,
                        identity=by_id[query_id]["human_identity"],
                        positive=by_id[query_id]["human_identity"],
                        align_length=100,
                        e_value=1e-20,
                        score=200,
                    )
                ],
            )
            for query_id, _sequence in queries
        ], "fixture-human"

    monkeypatch.setattr(
        runner_additions.blastdb_local,
        "blastp_with_remote_fallback",
        run_human_homology,
    )

    alg_session: dict[str, Any] = {
        "surface_exposed": {"candidates": representative_candidates}
    }
    vaxijen_session: dict[str, Any] = {
        "non_allergenic": {"candidates": representative_candidates}
    }
    vfdb_session: dict[str, Any] = {
        "antigenic": {"candidates": representative_candidates}
    }
    human_session: dict[str, Any] = {
        "virulence_factors": {"candidates": representative_candidates}
    }
    algpred_result = asyncio.run(runner_additions.run_3_1(alg_session, None, None))
    vaxijen_result = asyncio.run(runner_additions.run_3_2(vaxijen_session, None, None))
    vfdb_result = asyncio.run(runner_additions.run_3_3(vfdb_session, None, None))
    human_result = asyncio.run(runner_additions.run_3_4(human_session, None, None))

    def ids(session: dict[str, Any], stage: str) -> list[str]:
        return [candidate["uniprotId"] for candidate in (session.get(stage) or {}).get("candidates", [])]

    actual = {
        "cdhit": {
            "count": len(clusters),
            "ids": [candidates[index]["uniprotId"] for index in representatives],
        },
        "algpred": {"count": algpred_result.get("non_allergen_count", 0), "ids": ids(alg_session, "non_allergenic")},
        "vaxijen": {"count": vaxijen_result.get("antigenic_count", 0), "ids": ids(vaxijen_session, "antigenic")},
        "vfdb": {"count": vfdb_result.get("virulence_count", 0), "ids": ids(vfdb_session, "virulence_factors")},
        "human_homology": {
            "count": human_result.get("non_homologous_count", 0),
            "ids": ids(human_session, "vaccine_targets"),
        },
    }

    assert actual == expected
