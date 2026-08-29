from __future__ import annotations

import pytest

from app.models import JobCreate
from app.repo import repo
from app.tools import runner_additions
from app.tools.graceful_pause import ToolUnavailableError


def _job():
    repo._jobs.clear()
    repo._events.clear()
    return repo.create(JobCreate(name="remaining blocker regression", taxonId=208435))


@pytest.mark.asyncio
async def test_immunogenicity_uses_real_iedb_percentile_and_is_local_analysis():
    job = _job()
    session = {
        "epitopes": [{
            "id": "ctl-real-rank",
            "type": "CTL",
            "sequence": "SVPNKLSYL",
            "hlaAllele": "HLA-A*02:01",
            "percentileRank": 0.5,
            "ic50": None,
        }]
    }

    result = await runner_additions.run_5_5(session, job, None)

    assert result["source"] == "local-analysis"
    assert result["provenance"]["syntheticValues"] is False
    assert "real IEDB percentile rank proxy" in result["provenance"]["inputs"]
    epitope = session["epitopes"][0]
    assert epitope["immunogenicityScore"] is not None
    assert epitope["immunogenicity_method"] == "immunogenicity_local_iedb_inputs"
    assert epitope["immunogenicityInputs"]["percentileRank"] == 0.5
    assert "IEDB" not in epitope["immunogenicity_method"]


@pytest.mark.asyncio
async def test_immunogenicity_still_pauses_without_real_binding_input():
    job = _job()
    session = {
        "epitopes": [{
            "id": "ctl-incomplete",
            "type": "CTL",
            "sequence": "SVPNKLSYL",
            "hlaAllele": "HLA-A*02:01",
        }]
    }

    with pytest.raises(ToolUnavailableError, match="IC50 or percentile rank"):
        await runner_additions.run_5_5(session, job, None)


@pytest.mark.asyncio
async def test_overlap_uses_selected_real_epitopes_and_records_method():
    session = {
        "epitopes": [
            {"id": "ctl-1", "type": "CTL", "sequence": "ACDEFGHIK", "selected": True},
            {"id": "htl-1", "type": "HTL", "sequence": "LMNPQRSTVWY", "selected": True},
            {"id": "b-1", "type": "BCELL_LINEAR", "sequence": "DEFG", "selected": True},
            {"id": "b-ignored", "type": "BCELL_LINEAR", "sequence": "ACDE", "selected": False},
            {"id": "b-2", "type": "BCELL_CONFORMATIONAL", "sequence": "ZZZZ", "selected": True},
        ],
        # A stale conservancy subset must not hide selected B-cell rows from
        # the authoritative final epitope table.
        "conserved_epitopes": [{"id": "ctl-1", "type": "CTL", "sequence": "ACDEFGHIK"}],
    }

    result = await runner_additions.run_8_2(session, None, None)

    assert result["source"] == "local-analysis"
    assert result["provenance"]["syntheticValues"] is False
    assert result["overlap"]["bcell_count"] == 2
    assert result["overlap"]["tcell_count"] == 2
    assert result["overlap"]["overlap_count"] == 1
    assert result["overlap"]["overlap_ids"] == ["b-1"]
    assert result["overlap"]["overlap_pairs"][0]["overlapSequence"] == "DEFG"


@pytest.mark.asyncio
async def test_simulator_accepts_local_5_5_result_with_serialized_iedb_fields():
    from app.simulator import engine
    from app.tools.runner import get_session

    job = _job()
    step = next(step for phase in job.phases for step in phase.steps if step.id == "5-5")
    get_session(job.id)["epitopes"] = [{
        "id": "ctl-real-rank",
        "type": "CTL",
        "sequence": "SVPNKLSYL",
        "sourceProtein": "FIX-001",
        "hlaAllele": "HLA-A*02:01",
        "percentileRank": 0.5,
        "ic50": None,
        "selected": True,
    }]

    await engine._run_tool(job, step.phase, step)

    persisted = repo.get(job.id)
    assert persisted is not None
    persisted_step = next(s for phase in persisted.phases for s in phase.steps if s.id == "5-5")
    assert persisted_step.status == "success"
    assert persisted_step.result["provenance"]["status"] == "local-analysis"
    assert persisted_step.result["source"] == "local-analysis"


@pytest.mark.asyncio
async def test_simulator_accepts_local_8_2_result_from_authoritative_session_rows():
    from app.simulator import engine
    from app.tools.runner import get_session

    job = _job()
    step = next(step for phase in job.phases for step in phase.steps if step.id == "8-2")
    get_session(job.id)["epitopes"] = [
        {"id": "ctl-1", "type": "CTL", "sequence": "ACDEFGHIK", "selected": True},
        {"id": "b-1", "type": "BCELL_LINEAR", "sequence": "DEFG", "selected": True},
        {"id": "b-ignored", "type": "BCELL_LINEAR", "sequence": "ACDE", "selected": False},
    ]

    await engine._run_tool(job, step.phase, step)

    persisted = repo.get(job.id)
    assert persisted is not None
    persisted_step = next(s for phase in persisted.phases for s in phase.steps if s.id == "8-2")
    assert persisted_step.status == "success"
    assert persisted_step.result["provenance"]["status"] == "local-analysis"
    assert persisted_step.result["overlap"]["bcell_count"] == 1
    assert persisted_step.result["overlap"]["overlap_ids"] == ["b-1"]


@pytest.mark.asyncio
async def test_overlap_does_not_reintroduce_stale_rows_when_authoritative_table_is_empty():
    result = await runner_additions.run_8_2({
        "epitopes": [],
        "conserved_epitopes": [{
            "id": "stale-ctl",
            "type": "CTL",
            "sequence": "ACDEFGHIK",
        }],
    }, None, None)

    assert result["total_analyzed"] == 0
    assert result["overlap"]["bcell_count"] == 0
    assert result["overlap"]["tcell_count"] == 0


@pytest.mark.asyncio
async def test_immunogenicity_accepts_the_runner_serialized_iedb_row_contract():
    from app.tools import iedb
    from app.tools.runner import _serialize_epitopes

    job = _job()
    rows = _serialize_epitopes([
        iedb.EpitopePrediction(
            allele="HLA-A*02:01",
            seq_num=1,
            start=1,
            end=9,
            length=9,
            peptide="SVPNKLSYL",
            ic50=None,
            percentile_rank=0.5,
        ),
    ], kind="CTL", candidates=[{
        "index": 0,
        "uniprotId": "FIX-001",
        "name": "fixture protein",
        "sequence": "SVPNKLSYL",
    }])

    result = await runner_additions.run_5_5({"epitopes": rows}, job, None)

    assert rows[0]["hlaAllele"] == "HLA-A*02:01"
    assert rows[0]["percentileRank"] == 0.5
    assert rows[0]["ic50"] is None
    assert result["source"] == "local-analysis"
    assert result["provenance"]["status"] == "local-analysis"
    assert result["provenance"]["syntheticValues"] is False
    assert rows[0]["immunogenicityScore"] is not None


@pytest.mark.asyncio
async def test_protparam_accepts_valid_mev_sequence_and_records_local_provenance():
    sequence = "ACDEFGHIKLMNPQRSTVWY"
    session = {"mev_construct": {"sequence": sequence}}

    result = await runner_additions.run_10_1(session, None, None)

    from Bio.SeqUtils.ProtParam import ProteinAnalysis

    expected = ProteinAnalysis(sequence)
    measured = result["physicochemical"]
    assert result["source"] == "local-analysis"
    assert result["method"] == "protparam_local_biopython"
    assert result["provenance"]["status"] == "local-analysis"
    assert result["provenance"]["syntheticValues"] is False
    assert result["provenance"]["inputSource"] == "session['mev_construct']['sequence']"
    assert result["provenance"]["inputLength"] == len(sequence)
    assert set(result["provenance"]["measuredProperties"]) == set(measured)
    assert measured["molecular_weight"] == round(expected.molecular_weight(), 2)
    assert measured["isoelectric_point"] == round(expected.isoelectric_point(), 2)
    assert measured["instability_index"] == round(expected.instability_index(), 2)
    assert measured["gravy"] == round(expected.gravy(), 4)
    assert isinstance(measured["aromaticity"], float)
    assert isinstance(measured["charge_at_pH7"], float)
    assert isinstance(measured["extinction_coefficient"], int)
    assert isinstance(measured["aliphatic_index"], float)
    assert session["physicochemical"] == measured


@pytest.mark.asyncio
async def test_protparam_requires_a_non_empty_mev_construct_sequence():
    with pytest.raises(ToolUnavailableError, match="non-empty validated real MEV construct"):
        await runner_additions.run_10_1({}, None, None)


@pytest.mark.asyncio
async def test_protparam_pauses_for_invalid_mev_construct_sequence():
    with pytest.raises(ToolUnavailableError, match="not a valid amino-acid sequence"):
        await runner_additions.run_10_1(
            {"mev_construct": {"sequence": "ACDEFGHIKLMNPQRSTVWYZX"}},
            None,
            None,
        )


@pytest.mark.asyncio
async def test_simulator_persists_protparam_input_pause():
    from app.simulator import engine
    from app.tools.runner import get_session

    job = _job()
    step = next(step for phase in job.phases for step in phase.steps if step.id == "10-1")
    get_session(job.id)["mev_construct"] = {"sequence": ""}

    await engine._run_tool(job, step.phase, step)

    persisted = repo.get(job.id)
    assert persisted is not None
    persisted_step = next(s for phase in persisted.phases for s in phase.steps if s.id == "10-1")
    assert persisted_step.status == "paused"
    assert persisted_step.result["_paused"] is True
    assert "non-empty validated real MEV construct" in persisted_step.result["reason"]
