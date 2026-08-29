"""Task 20.1 pre-change safety baselines for the official SWISS-MODEL path.

All inputs are local fixtures.  These tests intentionally exercise the
current pause/manual-attachment behavior; they do not model an endpoint,
request schema, authorization value, or live provider response.
"""
from __future__ import annotations

import logging
from unittest.mock import AsyncMock

import pytest

from app.models import JobCreate, MEVStructureInput
from app.repo import repo
from app.routes import attach_structure_model, structure_requirements
from app.simulator import engine
from app.tools import alphafold, runner_additions
from app.tools.graceful_pause import ToolUnavailableError
from app.tools.runner import clear_session, get_session

from .swissmodel_contract_fixtures import (
    FakeOfficialContractTransport,
    CompletedStep92Mev,
    assert_public_sinks_safe,
    completed_step_9_2_mev,
    exact_mev_pdb,
)


def _step(job, step_id: str):
    return next(step for phase in job.phases for step in phase.steps if step.id == step_id)


def _completed_mev_job(mev: CompletedStep92Mev, name: str):
    """Create a job whose completed step 9-2 and runner session agree exactly."""
    job = repo.create(JobCreate(name=name, taxonId=1))
    assembly = _step(job, "9-2")
    assembly.status = "success"
    assembly.result = mev.step_result()
    repo.upsert(job)
    get_session(job.id).update(mev.runner_session())
    return job


def _public_sinks(job_id: str, caplog, transport: FakeOfficialContractTransport) -> dict:
    job = repo.get(job_id)
    assert job is not None
    return {
        "job": job.model_dump(mode="json"),
        "events": [event.model_dump(mode="json") for event in repo.events(job_id)],
        "logs": list(caplog.messages),
        "cache": transport.cache_sink,
    }


@pytest.mark.asyncio
async def test_current_step_11_2_pauses_without_manual_model_or_official_transport(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The pre-change exact-MEV path pauses; it cannot fabricate a structure."""
    monkeypatch.delenv("SWISSMODEL_API_TOKEN", raising=False)
    caplog.set_level(logging.DEBUG)
    transport = FakeOfficialContractTransport()
    mev = completed_step_9_2_mev()
    job = _completed_mev_job(mev, "official pause-only baseline")

    try:
        await engine._run_tool(job, 11, _step(job, "11-2"))

        persisted = repo.get(job.id)
        assert persisted is not None
        structure_step = _step(persisted, "11-2")
        assert structure_step.status == "paused"
        assert structure_step.result["_paused"] is True
        assert structure_step.result["tool"] == "AlphaFold/SwissModel"
        assert "No validated external structure" in structure_step.result["reason"]
        assert "structures" not in get_session(job.id)
        assert "validated_coordinate_data" not in get_session(job.id)

        # The test-only transport/cache sink is intentionally untouched while
        # no authenticated official adapter exists.
        transport.assert_unused()
        assert_public_sinks_safe(_public_sinks(job.id, caplog, transport))
    finally:
        clear_session(job.id)
        repo.delete(job.id)


@pytest.mark.asyncio
async def test_absent_token_explicit_provider_selection_makes_zero_transport_calls(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Selecting SWISS-MODEL without runtime configuration stays a no-call pause."""
    monkeypatch.setenv("MEV_STRUCTURE_PROVIDER", "swissmodel")
    monkeypatch.delenv("SWISSMODEL_API_TOKEN", raising=False)
    caplog.set_level(logging.DEBUG)
    transport = FakeOfficialContractTransport()
    unexpected_alphafold_lookup = AsyncMock()
    monkeypatch.setattr(alphafold, "fetch_prediction", unexpected_alphafold_lookup)

    with pytest.raises(ToolUnavailableError) as exc_info:
        await runner_additions.run_4_2_structure(
            completed_step_9_2_mev().runner_session(), None, None
        )

    assert exc_info.value.tool_name == "SWISS-MODEL"
    assert "no SWISSMODEL_API_TOKEN is configured" in exc_info.value.reason
    unexpected_alphafold_lookup.assert_not_awaited()
    transport.assert_unused()
    assert_public_sinks_safe({
        "publicPause": {
            "tool": exc_info.value.tool_name,
            "reason": exc_info.value.reason,
            "workaround": exc_info.value.workaround,
        },
        "logs": list(caplog.messages),
        "cache": transport.cache_sink,
    })


@pytest.mark.asyncio
@pytest.mark.parametrize("attachment_kind", ("pdb", "https"))
async def test_manual_pdb_and_https_attachment_remain_independent_of_official_transport(
    attachment_kind: str,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Manual exact-MEV PDB inputs still succeed without an official token."""
    monkeypatch.delenv("SWISSMODEL_API_TOKEN", raising=False)
    caplog.set_level(logging.DEBUG)
    transport = FakeOfficialContractTransport()
    mev = completed_step_9_2_mev()
    coordinate_text = exact_mev_pdb(mev.normalized_sequence)
    job = _completed_mev_job(mev, f"manual {attachment_kind} preservation baseline")
    model_url = "https://manual-model.example.test/exact-mev.pdb"

    try:
        requirements_before = structure_requirements(job.id)
        assert requirements_before["attachmentStatus"] == "missing"
        assert requirements_before["sequence"] == mev.normalized_sequence
        assert requirements_before["sequenceFingerprint"] == mev.fingerprint

        attachment_data = {
            "sequence": mev.normalized_sequence,
            "provider": "manual PDB attachment",
            "method": "manual exact-MEV PDB",
            "modelFormat": "pdb",
            "fileName": "exact-mev.pdb",
        }
        coordinate_fetch = None
        if attachment_kind == "pdb":
            attachment_data["coordinateText"] = coordinate_text
        else:
            attachment_data["modelUrl"] = model_url
            coordinate_fetch = AsyncMock(return_value=coordinate_text)
            monkeypatch.setattr(
                runner_additions,
                "_fetch_external_structure_coordinates",
                coordinate_fetch,
            )

        attached = attach_structure_model(job.id, MEVStructureInput(**attachment_data))
        requirements_after = structure_requirements(job.id)
        assert attached["status"] == "attached"
        assert requirements_after["attachmentStatus"] == "attached"

        await engine._run_tool(job, 11, _step(job, "11-2"))
        persisted = repo.get(job.id)
        assert persisted is not None
        structure_step = _step(persisted, "11-2")
        assert structure_step.status == "success"
        assert structure_step.result["source"] == "user-provided"
        validation = structure_step.result["sequenceIdentityValidation"]
        assert validation["identityPercent"] == 100.0
        assert validation["coveragePercent"] == 100.0
        assert validation["sequenceFingerprint"] == mev.fingerprint
        assert structure_step.result["coordinateDataAvailable"] is True
        assert structure_step.result["modelUrl"] == (None if attachment_kind == "pdb" else model_url)
        if coordinate_fetch is not None:
            coordinate_fetch.assert_awaited_once_with(model_url)

        session = get_session(job.id)
        assert session["validated_coordinate_data"][0]["coordinateText"] == coordinate_text
        assert "coordinateText" not in str(session["structures"])
        transport.assert_unused()
        assert_public_sinks_safe(
            {
                "attachment": attached,
                "requirementsBefore": requirements_before,
                "requirementsAfter": requirements_after,
                "sessionStructureMetadata": session["structures"],
                **_public_sinks(job.id, caplog, transport),
            },
            transient_coordinate_text=coordinate_text,
        )
    finally:
        clear_session(job.id)
        repo.delete(job.id)
