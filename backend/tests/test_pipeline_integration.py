"""Offline integration coverage for the pipeline-fixes behavior.

The fixtures in this module stand in for external services only.  The engine,
IEDB runner, localization runners, funnel updates, and repository remain the
production implementations under test.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.models import JobCreate
from app.repo import repo
from app.simulator import engine
from app.tools import api_cache, iedb
from app.tools import psortb_docker, runner as runner_mod
from app.tools import runner_additions
from app.tools.graceful_pause import ToolUnavailableError


class _OneRowIEDBPost:
    """Deterministic service fixture returning one strong row per query."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def __call__(self, _client: Any, url: str, data: dict[str, str]) -> Any:
        self.calls.append({"url": url, "data": data.copy()})
        mhcii = "mhcii" in url
        length = 15 if mhcii else 9
        peptide = ("ACDEFGHIKLMNPQR" if mhcii else "ACDEFGHIK")[:length]
        header = (
            "allele\tseq_num\tstart\tend\tlength\tcore_peptide\tpeptide\tscore\trank"
            if mhcii
            else "allele\tseq_num\tstart\tend\tlength\tpeptide\tcore\tscore\tpercentile_rank"
        )
        allele = data["allele"].split(",")[0]
        query_count = sum(line.startswith(">") for line in data["sequence_text"].splitlines())
        rows = [
            f"{allele}\t{seq_num}\t1\t{length}\t{length}\t{peptide}\t{peptide}\t0.9\t0.5"
            for seq_num in range(1, query_count + 1)
        ]
        return type("Response", (), {"text": header + "\n" + "\n".join(rows), "status_code": 200})()


def _reset_state() -> None:
    repo._jobs.clear()
    repo._events.clear()
    runner_mod._RUN_SESSION.clear()


def _fixture_job(name: str) -> Any:
    return repo.create(
        JobCreate(
            name=name,
            pathogenName="Streptococcus agalactiae",
            taxonId=208435,
            hlaMhc1=["HLA-A*02:01"],
            hlaMhc2=["HLA-DRB1*01:01"],
            realTools=True,
        )
    )


def _seed_essential(job: Any) -> None:
    candidates = [
        {
            "index": index,
            "uniprotId": f"FIX-INTEGRATION-{index}",
            "name": f"Fixture protein {index}",
            "sequence": "M" + "ACDEFGHIKLMNPQRSTVWY" * 5,
        }
        for index in range(2)
    ]
    session = runner_mod.get_session(job.id)
    session["essential"] = {"indices": [0, 1], "candidates": candidates}
    session["_funnel_counts"] = {"proteins": 2, "redundant": 2, "essential": 2}


async def _drive_engine_fixture_run(
    job: Any,
    monkeypatch: pytest.MonkeyPatch,
    *,
    iedb_post: Any,
    fixture_runners: dict[str, Any] | None = None,
) -> Any:
    """Drive the real engine until completion or an honest external pause."""
    _seed_essential(job)
    monkeypatch.setattr(api_cache, "_read_cache", lambda *args, **kwargs: None)
    monkeypatch.setattr(api_cache, "_write_cache", lambda *args, **kwargs: None)

    async def no_wait(_seconds: float) -> None:
        return None

    monkeypatch.setattr(iedb.asyncio, "sleep", no_wait)
    monkeypatch.setattr(iedb, "_post", iedb_post)

    original_runners = dict(runner_mod.STEP_RUNNERS)
    fixture_runners = fixture_runners or {}

    async def fixture_runner(_job: Any, _step: Any) -> dict[str, Any]:
        return {"method": "user-provided integration fixture", "source": "user-provided"}

    for step_id in original_runners:
        if step_id not in {"5-1", "6-1"}:
            runner_mod.STEP_RUNNERS[step_id] = fixture_runners.get(step_id, fixture_runner)

    try:
        job.status = "running"
        await engine.reconcile_on_start(job)
        for _ in range(job.totalSteps + 3):
            current = repo.get(job.id)
            assert current is not None
            if current.status in {"completed", "paused", "failed"}:
                return current
            await engine._advance(current)
        pytest.fail("fixture engine did not reach completion or an external pause")
    finally:
        runner_mod.STEP_RUNNERS.clear()
        runner_mod.STEP_RUNNERS.update(original_runners)


@pytest.mark.asyncio
async def test_full_engine_run_pauses_honestly_when_iedb_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An IEDB outage pauses the run and never emits synthetic epitope rows."""
    _reset_state()
    post = AsyncMock(side_effect=ConnectionError("IEDB fixture outage"))
    job = _fixture_job("integration IEDB failure")
    final = await _drive_engine_fixture_run(job, monkeypatch, iedb_post=post)

    assert final.status == "paused"
    ctl_step = next(step for phase in final.phases for step in phase.steps if step.id == "5-1")
    assert ctl_step.status == "paused"
    assert ctl_step.result and ctl_step.result["_paused"] is True
    assert "no synthetic" in ctl_step.result["message"].lower()
    assert final.epitopes == []


@pytest.mark.asyncio
async def test_full_engine_happy_path_is_deterministic_against_successful_baseline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Property 8: successful IEDB output keeps the existing funnel/epitope path."""
    _reset_state()
    first_post = _OneRowIEDBPost()
    first = await _drive_engine_fixture_run(
        _fixture_job("integration IEDB baseline"), monkeypatch, iedb_post=first_post
    )
    first_funnel = first.funnel
    first_epitopes = [epitope.model_dump(mode="json") for epitope in first.epitopes]

    _reset_state()
    second_post = _OneRowIEDBPost()
    second = await _drive_engine_fixture_run(
        _fixture_job("integration IEDB repeat"), monkeypatch, iedb_post=second_post
    )
    assert second.status == "completed"
    assert second.stepsCompleted == second.totalSteps
    assert second.funnel == first_funnel
    assert [epitope.model_dump(mode="json") for epitope in second.epitopes] == first_epitopes
    assert all(epitope.source == "real" for epitope in second.epitopes)
    assert all("fallback" not in (epitope.predictionMethod or "").lower() for epitope in second.epitopes)
    assert len(first_post.calls) == len(second_post.calls) == 2


@pytest.mark.asyncio
async def test_full_fifty_step_official_success_and_pause_fixtures_are_isolated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Task 20.9: only Step 11-2 differs across local official outcomes."""
    from .swissmodel_contract_fixtures import (
        assert_public_sinks_safe,
        completed_step_9_2_mev,
        exact_mev_pdb,
    )

    monkeypatch.setenv("MEV_STRUCTURE_PROVIDER", "swissmodel")
    monkeypatch.delenv("SWISSMODEL_API_TOKEN", raising=False)
    mev = completed_step_9_2_mev()
    coordinate_text = exact_mev_pdb(mev.normalized_sequence)
    model_url = "https://swissmodel.expasy.org/project/fixture-project/model/fixture-model/pdb/"
    success_calls: list[str] = []
    paused_calls: list[str] = []

    async def completed_mev_runner(job: Any, _step: Any) -> dict[str, Any]:
        runner_mod.get_session(job.id)["mev_construct"] = {
            "sequence": mev.normalized_sequence,
        }
        return {
            **mev.step_result(),
            "source": "user-provided",
            "method": "exact-MEV integration fixture",
        }

    async def official_success_runner(job: Any, _step: Any) -> dict[str, Any]:
        session = runner_mod.get_session(job.id)
        assert session["mev_construct"]["sequence"] == mev.normalized_sequence
        success_calls.append(mev.normalized_sequence)
        session["validated_coordinate_data"] = [{
            "sequence": mev.normalized_sequence,
            "coordinateText": coordinate_text,
            "coordinateSourceUrl": model_url,
            "provider": "SWISS-MODEL",
            "method": "official fixture automodel",
            "source": "real",
            "metadata": {},
        }]
        return {
            "message": "Validated official fixture model accepted for the exact assembled sequence",
            "source": "real",
            "provider": "SWISS-MODEL",
            "method": "automodel",
            "modelUrl": model_url,
            "sequenceIdentityValidation": {
                "identityPercent": 100.0,
                "coveragePercent": 100.0,
                "sequenceFingerprint": mev.fingerprint,
            },
            "coordinateDataAvailable": True,
            "syntheticValues": False,
            "provenance": {
                "status": "real",
                "provider": "swissmodel",
                "method": "automodel",
                "modelUrl": model_url,
                "sequenceFingerprint": mev.fingerprint,
            },
            "officialLifecycle": {
                "status": "succeeded",
                "provider": "swissmodel",
                "mevFingerprint": mev.fingerprint,
                "method": "automodel",
                "requestId": "fixture-project",
                "modelId": "fixture-model",
                "modelUrl": model_url,
                "submittedAt": "2025-01-02T03:04:05+00:00",
                "completedAt": "2025-01-02T03:06:05+00:00",
            },
        }

    async def official_paused_runner(job: Any, _step: Any) -> dict[str, Any]:
        session = runner_mod.get_session(job.id)
        assert session["mev_construct"]["sequence"] == mev.normalized_sequence
        paused_calls.append(mev.normalized_sequence)
        raise ToolUnavailableError(
            tool_name="SWISS-MODEL",
            reason="The local official lifecycle fixture paused before any validated coordinates existed.",
            workaround="Attach a validated external PDB model before retrying Step 11-2.",
            public_status={
                "status": "paused",
                "provider": "swissmodel",
                "mevFingerprint": mev.fingerprint,
                "method": "automodel",
                "messageCode": "swissmodel_fixture_paused",
                "action": "Attach a validated external PDB model.",
            },
        )

    def step(job: Any, step_id: str) -> Any:
        return next(candidate for phase in job.phases for candidate in phase.steps if candidate.id == step_id)

    def upstream_snapshot(job: Any) -> dict[str, dict[str, Any]]:
        return {
            candidate.id: {
                "status": candidate.status,
                "percent": candidate.percent,
                "duration": candidate.duration,
                "result": candidate.result,
            }
            for phase in job.phases
            for candidate in phase.steps
            if phase.number < 11 or candidate.id == "11-1"
        }

    def unrelated_counts(job: Any) -> dict[str, Any]:
        return {
            "funnel": job.funnel,
            "epitopeCount": len(job.epitopes),
            "epitopes": [epitope.model_dump(mode="json") for epitope in job.epitopes],
        }

    _reset_state()
    successful = await _drive_engine_fixture_run(
        _fixture_job("Task 20.9 official fixture success"),
        monkeypatch,
        iedb_post=_OneRowIEDBPost(),
        fixture_runners={
            "9-2": completed_mev_runner,
            "11-2": official_success_runner,
        },
    )
    successful_public = successful.model_dump(mode="json")
    successful_events = [event.model_dump(mode="json") for event in repo.events(successful.id)]
    successful_session = runner_mod.get_session(successful.id)

    _reset_state()
    paused = await _drive_engine_fixture_run(
        _fixture_job("Task 20.9 official fixture pause"),
        monkeypatch,
        iedb_post=_OneRowIEDBPost(),
        fixture_runners={
            "9-2": completed_mev_runner,
            "11-2": official_paused_runner,
        },
    )
    paused_public = paused.model_dump(mode="json")
    paused_events = [event.model_dump(mode="json") for event in repo.events(paused.id)]
    paused_session = runner_mod.get_session(paused.id)

    assert success_calls == [mev.normalized_sequence]
    assert paused_calls == [mev.normalized_sequence]
    assert successful.totalSteps == paused.totalSteps == 50
    assert successful.status == "completed"
    assert successful.stepsCompleted == 50
    assert paused.status == "paused"

    successful_step = step(successful, "11-2")
    paused_step = step(paused, "11-2")
    assert successful_step.status == "success"
    assert successful_step.result["officialLifecycle"]["status"] == "succeeded"
    assert successful_step.result["provenance"]["sequenceFingerprint"] == mev.fingerprint
    assert paused_step.status == "paused"
    assert paused_step.result["officialLifecycle"]["status"] == "paused"
    assert paused_step.result["officialLifecycle"]["mevFingerprint"] == mev.fingerprint

    # All pre-structure pipeline decisions and counts are identical. The pause
    # leaves later structure/analysis steps unstarted rather than fabricating data.
    assert upstream_snapshot(successful) == upstream_snapshot(paused)
    assert unrelated_counts(successful) == unrelated_counts(paused)
    assert successful_session["validated_coordinate_data"][0]["coordinateText"] == coordinate_text
    assert "structures" not in paused_session
    assert "validated_coordinate_data" not in paused_session
    for phase in paused.phases:
        for candidate in phase.steps:
            if phase.number > 11 or (phase.number == 11 and candidate.number > 2):
                assert candidate.status == "pending"
                assert candidate.result is None

    assert "coordinateText" not in str(successful_public)
    assert "coordinateText" not in str(paused_public)
    assert_public_sinks_safe(
        successful_public,
        successful_events,
        paused_public,
        paused_events,
        transient_coordinate_text=coordinate_text,
    )


class _PsortbFixtureClient:
    async def localize(self, sequences: list[str], *, gram: str = "positive") -> list[dict[str, Any]]:
        del gram
        labels = ["OuterMembrane", "Lipoprotein", "CellWall", "Cytoplasmic", "Extracellular"]
        return [
            {"localization": labels[index % len(labels)], "score": 1.0, "cached": False}
            for index, _sequence in enumerate(sequences)
        ]


class _PhobiusFixtureClient:
    async def submit(self, _tool: str, _sequence: str) -> str:
        return "fixture-job"

    async def poll_status(self, _tool: str, _job_id: str) -> str:
        return "FINISHED"

    async def fetch_result(self, _tool: str, _job_id: str, _result_type: str = "out") -> str:
        return "ID FIXTURE\n//"

    async def close(self) -> None:
        return None


@pytest.fixture
def localization_candidates(fixture_proteome: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "index": protein["index"],
            "uniprotId": protein["uniprotId"],
            "name": protein["name"],
            "sequence": protein["sequence"],
        }
        for protein in fixture_proteome
    ]


def _localization_session(candidates: list[dict[str, Any]]) -> dict[str, Any]:
    return {"essential": {"candidates": candidates}, "_funnel_counts": {}}


@pytest.mark.asyncio
async def test_localization_binary_present_improves_surface_count_and_unions_phobius(
    monkeypatch: pytest.MonkeyPatch,
    localization_candidates: list[dict[str, Any]],
) -> None:
    """Property 4: real PSORTb categories survive Phobius refinement."""
    session = _localization_session(localization_candidates)
    monkeypatch.setattr(runner_additions, "find_psortb_binary", lambda: "/fixture/psortb")
    monkeypatch.setattr(runner_additions, "PSORTbClient", lambda **_kwargs: _PsortbFixtureClient())
    monkeypatch.setattr(runner_additions, "EBIRestClient", lambda **_kwargs: _PhobiusFixtureClient())
    monkeypatch.setattr(runner_additions, "PHOBIUS_RATE_LIMIT_SEC", 0.0)
    monkeypatch.setattr(runner_additions, "_load_phobius_cache", lambda: {})
    monkeypatch.setattr(runner_additions, "_save_phobius_cache", lambda _cache: None)

    psortb_result = await runner_additions.run_2_2(session, None, None)
    final_result = await runner_additions.run_2_4(session, None, None)

    assert psortb_result["surface_exposed_count"] == 4
    assert final_result["surface_exposed_count"] == 4
    assert final_result["provenance"]["psortbAvailable"] is True
    assert {candidate["uniprotId"] for candidate in session["surface_exposed"]["candidates"]} == {
        "FIX-OM-001", "FIX-LIPO-001", "FIX-WALL-001", "FIX-SECRETED-001"
    }
    assert "FIX-INTRA-001" not in {
        candidate["uniprotId"] for candidate in session["surface_exposed"]["candidates"]
    }


@pytest.mark.asyncio
async def test_localization_without_binary_keeps_phobius_scope_and_documents_residual(
    monkeypatch: pytest.MonkeyPatch,
    localization_candidates: list[dict[str, Any]],
) -> None:
    """Property 4: missing PSORTb is explicit, never silently scientific."""
    session = _localization_session(localization_candidates)
    monkeypatch.setattr(runner_additions, "find_psortb_binary", lambda: None)
    monkeypatch.setattr(
        psortb_docker,
        "require_available",
        lambda: (_ for _ in ()).throw(psortb_docker.PSORTbUnavailable("fixture binary unavailable")),
    )
    monkeypatch.setattr(runner_additions, "EBIRestClient", lambda **_kwargs: _PhobiusFixtureClient())
    monkeypatch.setattr(runner_additions, "PHOBIUS_RATE_LIMIT_SEC", 0.0)
    monkeypatch.setattr(runner_additions, "_load_phobius_cache", lambda: {})
    monkeypatch.setattr(runner_additions, "_save_phobius_cache", lambda _cache: None)

    psortb_result = await runner_additions.run_2_2(session, None, None)
    final_result = await runner_additions.run_2_4(session, None, None)

    assert psortb_result["status"] == "partial"
    assert psortb_result["provenance"]["psortbAvailable"] is False
    assert "fixture binary unavailable" in psortb_result["residual_gap"]
    assert final_result["provenance"]["psortbAvailable"] is False
    assert "fixture binary unavailable" in final_result["provenance"]["residualGap"]
    assert final_result["surface_exposed_count"] == 0
    assert session["surface_exposed"]["count"] == 0
