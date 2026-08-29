"""Task 20.2 tests for server-only SWISS-MODEL capability and preflight.

All provider interactions remain local fakes.  No test sends a request to
SWISS-MODEL or contains a literal credential.
"""
from __future__ import annotations

from dataclasses import asdict
import logging
import secrets
from unittest.mock import AsyncMock

import pytest

from app.models import JobCreate
from app.repo import repo
from app.simulator import engine
from app.tools import runner_additions, swissmodel_runtime
from app.tools.runner import clear_session, get_session

from .swissmodel_contract_fixtures import (
    FakeOfficialContractTransport,
    assert_public_sinks_safe,
    completed_step_9_2_mev,
)


def _step(job, step_id: str):
    return next(step for phase in job.phases for step in phase.steps if step.id == step_id)


def _completed_mev_job(name: str):
    mev = completed_step_9_2_mev()
    job = repo.create(JobCreate(name=name, taxonId=1))
    assembly = _step(job, "9-2")
    assembly.status = "success"
    assembly.result = mev.step_result()
    repo.upsert(job)
    get_session(job.id).update(mev.runner_session())
    return job, mev


def test_missing_runtime_configuration_returns_actionable_paused_preflight(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SWISSMODEL_API_TOKEN", raising=False)
    mev = completed_step_9_2_mev()

    preflight = swissmodel_runtime.preflight_swissmodel_submission(
        step_9_2_completed=True,
        step_9_2_sequence=mev.raw_sequence,
        current_mev_sequence=mev.normalized_sequence,
        current_mev_fingerprint=mev.fingerprint,
    )

    assert preflight.ready is False
    assert preflight.public_result() == {
        "status": "paused",
        "provider": "swissmodel",
        "messageCode": "swissmodel_runtime_secret_missing",
        "action": "Configure the SWISS-MODEL server runtime secret, then retry Step 11-2.",
    }
    assert_public_sinks_safe(preflight.public_result())


def test_preflight_requires_current_mev_to_match_completed_step_9_2_fingerprint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Generated only in process for this test; it is never logged or serialized.
    monkeypatch.setenv("SWISSMODEL_API_TOKEN", secrets.token_urlsafe(32))
    mev = completed_step_9_2_mev()
    changed_sequence = "AVLA"

    preflight = swissmodel_runtime.preflight_swissmodel_submission(
        step_9_2_completed=True,
        step_9_2_sequence=mev.normalized_sequence,
        current_mev_sequence=changed_sequence,
        current_mev_fingerprint=swissmodel_runtime.mev_sequence_fingerprint(changed_sequence),
    )

    assert preflight.ready is False
    assert preflight.status == "paused"
    assert preflight.message_code == "swissmodel_mev_fingerprint_mismatch"
    assert_public_sinks_safe(preflight.public_result())


@pytest.mark.asyncio
async def test_missing_configuration_pauses_before_any_provider_or_coordinate_call(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setenv("MEV_STRUCTURE_PROVIDER", "swissmodel")
    monkeypatch.delenv("SWISSMODEL_API_TOKEN", raising=False)
    caplog.set_level(logging.DEBUG)
    transport = FakeOfficialContractTransport()
    unexpected_coordinate_fetch = AsyncMock(
        side_effect=AssertionError("preflight must not fetch provider coordinates")
    )
    monkeypatch.setattr(
        runner_additions,
        "_fetch_external_structure_coordinates",
        unexpected_coordinate_fetch,
    )
    job, _mev = _completed_mev_job("SwissModel missing runtime preflight")

    try:
        await engine._run_tool(job, 11, _step(job, "11-2"))

        persisted = repo.get(job.id)
        assert persisted is not None
        structure_step = _step(persisted, "11-2")
        assert structure_step.status == "paused"
        assert structure_step.result["_paused"] is True
        assert structure_step.result["tool"] == "SWISS-MODEL"
        assert structure_step.result["reason"] == (
            "no SWISSMODEL_API_TOKEN is configured in the server runtime."
        )
        assert "Configure the SWISS-MODEL server runtime secret" in structure_step.result["workaround"]
        unexpected_coordinate_fetch.assert_not_awaited()
        transport.assert_unused()
        assert_public_sinks_safe({
            "job": persisted.model_dump(mode="json"),
            "events": [event.model_dump(mode="json") for event in repo.events(job.id)],
            "logs": list(caplog.messages),
            "cache": transport.cache_sink,
        })
    finally:
        clear_session(job.id)
        repo.delete(job.id)


@pytest.mark.asyncio
async def test_runtime_capability_never_serializes_credential_material(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Only opaque capability and public-safe preflight fields cross boundaries."""
    # This process-only generated value is intentionally never compared or
    # rendered, preventing it from becoming test output on a future failure.
    monkeypatch.setenv("SWISSMODEL_API_TOKEN", secrets.token_urlsafe(32))
    monkeypatch.setenv("MEV_STRUCTURE_PROVIDER", "swissmodel")
    caplog.set_level(logging.DEBUG)
    job, mev = _completed_mev_job("SwissModel capability redaction")
    transport = FakeOfficialContractTransport()

    try:
        capability = swissmodel_runtime.swissmodel_runtime_capability()
        preflight = swissmodel_runtime.preflight_swissmodel_submission(
            step_9_2_completed=True,
            step_9_2_sequence=mev.normalized_sequence,
            current_mev_sequence=mev.normalized_sequence,
            current_mev_fingerprint=mev.fingerprint,
        )
        assert capability.available is True
        assert set(asdict(capability)) == {"available", "message_code", "reason", "action"}
        assert preflight.ready is True
        assert set(preflight.public_result()) == {"status", "provider", "mevFingerprint"}
        assert "normalized_sequence" not in preflight.public_result()

        # With no Task 20.3 adapter, a capability-ready run still pauses rather
        # than performing an undocumented request.  Its public artifacts carry
        # only the fixed, safe pause information.
        await engine._run_tool(job, 11, _step(job, "11-2"))
        persisted = repo.get(job.id)
        assert persisted is not None
        structure_step = _step(persisted, "11-2")
        assert structure_step.status == "paused"
        assert "official-contract client" in structure_step.result["reason"]
        transport.assert_unused()
        assert_public_sinks_safe(
            {
                "capability": asdict(capability),
                "preflight": preflight.public_result(),
                "job": persisted.model_dump(mode="json"),
                "events": [event.model_dump(mode="json") for event in repo.events(job.id)],
                "logs": list(caplog.messages),
                "cache": transport.cache_sink,
                "session": get_session(job.id),
            }
        )
    finally:
        clear_session(job.id)
        repo.delete(job.id)
