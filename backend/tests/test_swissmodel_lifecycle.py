"""Task 20.4 lifecycle capability and public-status tests.

All provider interactions are local fakes.  The current official CoreAPI does
not document a complete authenticated create/poll/cancel/result lifecycle, so
these tests verify the required safe pause boundary rather than inventing an
HTTP operation or response schema.
"""
from __future__ import annotations

from datetime import datetime, timezone
import logging
import secrets

import pytest

from app.models import JobCreate
from app.repo import repo
from app.routes import get_job
from app.simulator import engine
from app.tools.runner import clear_session, get_session
from app.tools.swissmodel_contract import (
    OfficialAutomodelReceipt,
    SwissModelAutomodelAdapter,
)
from app.tools.swissmodel_lifecycle import (
    OFFICIAL_COREAPI_LIFECYCLE_CAPABILITY,
    SwissModelLifecycleAdapter,
    SwissModelLifecycleStatus,
    apply_verified_lifecycle_state,
)
from app.tools.swissmodel_runtime import preflight_swissmodel_submission

from .swissmodel_contract_fixtures import assert_public_sinks_safe, completed_step_9_2_mev


class RecordingAutomodelTransport:
    """A local fake for the documented direct automodel payload only."""

    def __init__(self, receipt: OfficialAutomodelReceipt):
        self.receipt = receipt
        self.target_sequences: list[str] = []

    async def create_automodel(self, *, target_sequences: str) -> OfficialAutomodelReceipt:
        self.target_sequences.append(target_sequences)
        return self.receipt


def _ready_preflight(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("SWISSMODEL_API_TOKEN", secrets.token_urlsafe(32))
    mev = completed_step_9_2_mev()
    return mev, preflight_swissmodel_submission(
        step_9_2_completed=True,
        step_9_2_sequence=mev.raw_sequence,
        current_mev_sequence=mev.normalized_sequence,
        current_mev_fingerprint=mev.fingerprint,
    )


def _receipt() -> OfficialAutomodelReceipt:
    return OfficialAutomodelReceipt(
        request_id="request-42",
        model_id="model-7",
        model_url="https://swissmodel.expasy.org/project/request-42/model/model-7/pdb/",
        submitted_at=datetime(2025, 1, 2, 3, 4, 5, tzinfo=timezone.utc),
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


@pytest.mark.asyncio
async def test_current_contract_pauses_before_authenticated_create_without_calling_direct_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The documented direct create must not be called with guessed token semantics."""
    _mev, preflight = _ready_preflight(monkeypatch)
    transport = RecordingAutomodelTransport(_receipt())

    status = await SwissModelLifecycleAdapter(
        SwissModelAutomodelAdapter(transport)
    ).create(preflight)

    assert OFFICIAL_COREAPI_LIFECYCLE_CAPABILITY.automodel_create is True
    assert OFFICIAL_COREAPI_LIFECYCLE_CAPABILITY.complete is False
    assert status.state == "paused"
    assert status.message_code == "swissmodel_official_lifecycle_unavailable"
    assert transport.target_sequences == []
    assert_public_sinks_safe(status.public_result())


@pytest.mark.asyncio
async def test_safe_lifecycle_status_can_represent_verified_queued_running_and_succeeded_progression(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A later documented parser can emit all safe progression states without raw payloads."""
    mev, preflight = _ready_preflight(monkeypatch)
    transport = RecordingAutomodelTransport(_receipt())
    submission = await SwissModelAutomodelAdapter(transport).submit(preflight)

    queued = SwissModelLifecycleStatus(
        state="queued",
        mev_fingerprint=submission.admission.mev_fingerprint,
        method=submission.admission.method,
        request_id=submission.request_id,
        model_id=submission.model_id,
        model_url=submission.model_url,
        submitted_at=submission.submitted_at,
        updated_at=submission.submitted_at,
    )
    running = apply_verified_lifecycle_state(
        queued,
        state="running",
        at=datetime(2025, 1, 2, 3, 5, 5, tzinfo=timezone.utc),
    )
    succeeded = apply_verified_lifecycle_state(
        running,
        state="succeeded",
        at=datetime(2025, 1, 2, 3, 6, 5, tzinfo=timezone.utc),
    )

    assert [queued.state, running.state, succeeded.state] == ["queued", "running", "succeeded"]
    assert transport.target_sequences == [mev.normalized_sequence]
    assert succeeded.public_result() == {
        "status": "succeeded",
        "provider": "swissmodel",
        "mevFingerprint": mev.fingerprint,
        "method": "automodel",
        "requestId": "request-42",
        "modelId": "model-7",
        "modelUrl": "https://swissmodel.expasy.org/project/request-42/model/model-7/pdb/",
        "submittedAt": "2025-01-02T03:04:05+00:00",
        "updatedAt": "2025-01-02T03:06:05+00:00",
        "completedAt": "2025-01-02T03:06:05+00:00",
    }
    assert "sequence" not in succeeded.public_result()
    assert_public_sinks_safe(queued.public_result(), running.public_result(), succeeded.public_result())


@pytest.mark.asyncio
async def test_verified_terminal_failure_is_sanitized_and_terminal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Provider failure maps to a code/action, never raw provider error content."""
    _mev, preflight = _ready_preflight(monkeypatch)
    submission = await SwissModelAutomodelAdapter(RecordingAutomodelTransport(_receipt())).submit(preflight)
    queued = SwissModelLifecycleStatus(
        state="queued",
        mev_fingerprint=submission.admission.mev_fingerprint,
        method=submission.admission.method,
        request_id=submission.request_id,
        submitted_at=submission.submitted_at,
    )

    failed = apply_verified_lifecycle_state(
        queued,
        state="failed",
        at=datetime(2025, 1, 2, 3, 7, 5, tzinfo=timezone.utc),
    )
    unchanged = apply_verified_lifecycle_state(
        failed,
        state="running",
        at=datetime(2025, 1, 2, 3, 8, 5, tzinfo=timezone.utc),
    )

    assert failed.public_result()["messageCode"] == "swissmodel_provider_terminal_failure"
    assert "raw" not in str(failed.public_result()).lower()
    assert unchanged.state == "failed"
    assert unchanged.public_result()["messageCode"] == "swissmodel_lifecycle_transition_invalid"
    assert_public_sinks_safe(failed.public_result(), unchanged.public_result())


@pytest.mark.asyncio
async def test_user_cancellation_only_pauses_a_recorded_non_terminal_job(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No cancellation call is guessed; terminal records remain untouched."""
    _mev, preflight = _ready_preflight(monkeypatch)
    submission = await SwissModelAutomodelAdapter(RecordingAutomodelTransport(_receipt())).submit(preflight)
    queued = SwissModelLifecycleStatus(
        state="queued",
        mev_fingerprint=submission.admission.mev_fingerprint,
        method=submission.admission.method,
        request_id=submission.request_id,
        submitted_at=submission.submitted_at,
    )
    lifecycle = SwissModelLifecycleAdapter()

    paused = await lifecycle.cancel(queued)
    succeeded = apply_verified_lifecycle_state(
        queued,
        state="succeeded",
        at=datetime(2025, 1, 2, 3, 9, 5, tzinfo=timezone.utc),
    )
    declined = await lifecycle.cancel(succeeded)

    assert paused.state == "paused"
    assert paused.public_result()["messageCode"] == "swissmodel_cancellation_unavailable"
    assert declined.state == "succeeded"
    assert declined.public_result()["messageCode"] == "swissmodel_cancellation_not_available"
    assert_public_sinks_safe(paused.public_result(), declined.public_result())


@pytest.mark.asyncio
async def test_paused_step_serializes_only_safe_official_lifecycle_status(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The existing Job API gains additive safe status with no credential or raw error."""
    monkeypatch.setenv("MEV_STRUCTURE_PROVIDER", "swissmodel")
    monkeypatch.setenv("SWISSMODEL_API_TOKEN", secrets.token_urlsafe(32))
    caplog.set_level(logging.DEBUG)
    job, mev = _completed_mev_job("SwissModel lifecycle public status")

    try:
        await engine._run_tool(job, 11, _step(job, "11-2"))

        api_payload = get_job(job.id).model_dump(mode="json")
        public_step = _step(get_job(job.id), "11-2")
        lifecycle = public_step.result["officialLifecycle"]
        assert public_step.status == "paused"
        assert lifecycle["status"] == "paused"
        assert lifecycle["provider"] == "swissmodel"
        assert lifecycle["messageCode"] == "swissmodel_official_lifecycle_unavailable"
        assert lifecycle["mevFingerprint"] == mev.fingerprint
        assert lifecycle["method"] == "automodel"
        assert "sequence" not in lifecycle
        assert_public_sinks_safe(
            api_payload,
            [event.model_dump(mode="json") for event in repo.events(job.id)],
            list(caplog.messages),
        )
    finally:
        clear_session(job.id)
        repo.delete(job.id)
