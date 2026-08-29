"""Task 20.5 bounded SWISS-MODEL resilience tests.

Every provider interaction is a local fake or an ``httpx.MockTransport``.  The
suite never contacts SWISS-MODEL and never persists/prints an authorization
value, provider body, raw request, or MEV sequence.
"""
from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
import json
import secrets
from typing import Any

import httpx
from hypothesis import assume, given, settings, strategies as st
import pytest

from app.tools.swissmodel_contract import OfficialAutomodelReceipt
from app.tools.swissmodel_lifecycle import SwissModelLifecycleStatus
from app.tools.swissmodel_resilience import (
    OfficialSwissModelCoreApiHttpTransport,
    OfficialSwissModelCoreApiRoutes,
    OfficialSwissModelModelSummary,
    OfficialSwissModelProject,
    SwissModelLifecycleKey,
    SwissModelLifecycleRecord,
    SwissModelLifecycleStore,
    SwissModelOperationError,
    SwissModelResilientLifecycle,
    SwissModelRetryPolicy,
)
from app.tools.swissmodel_runtime import preflight_swissmodel_submission

from .swissmodel_contract_fixtures import assert_public_sinks_safe, completed_step_9_2_mev


class ScriptedOfficialTransport:
    """Local typed outcomes for documented create/project/summary operations."""

    def __init__(
        self,
        *,
        create_outcomes: list[object],
        project_outcomes: list[object] | None = None,
        summary_outcomes: list[object] | None = None,
    ) -> None:
        self.create_outcomes = deque(create_outcomes)
        self.project_outcomes = deque(project_outcomes or [])
        self.summary_outcomes = deque(summary_outcomes or [])
        self.create_calls: list[str] = []
        self.project_calls: list[str] = []
        self.summary_calls: list[str] = []

    async def create_automodel(self, *, target_sequences: str) -> OfficialAutomodelReceipt:
        self.create_calls.append(target_sequences)
        return self._next(self.create_outcomes)

    async def read_project(self, *, project_id: str) -> OfficialSwissModelProject:
        self.project_calls.append(project_id)
        return self._next(self.project_outcomes)

    async def read_project_model_summary(
        self,
        *,
        project_id: str,
    ) -> OfficialSwissModelModelSummary:
        self.summary_calls.append(project_id)
        return self._next(self.summary_outcomes)

    @staticmethod
    def _next(outcomes: deque[object]) -> Any:
        if not outcomes:
            raise AssertionError("unexpected fake official operation")
        outcome = outcomes.popleft()
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _ready_preflight(monkeypatch: pytest.MonkeyPatch):
    # Generated only inside the test process; no assertion or public fixture
    # renders the value.
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
        request_id="project-42",
        model_id="model-7",
        model_url="https://swissmodel.expasy.org/project/project-42/model/model-7/pdb/",
        submitted_at=datetime(2025, 1, 2, 3, 4, 5, tzinfo=timezone.utc),
    )


async def _no_sleep(_seconds: float) -> None:
    """Injectable backoff hook that keeps rate-limit tests deterministic."""


@pytest.mark.asyncio
async def test_ambiguous_create_is_attempted_once_then_cached_as_a_safe_pause(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A timed-out create is never retried by the same exact input key."""
    mev, preflight = _ready_preflight(monkeypatch)
    transport = ScriptedOfficialTransport(
        create_outcomes=[SwissModelOperationError("timeout", retryable=True)],
    )
    lifecycle = SwissModelResilientLifecycle(
        transport,
        policy=SwissModelRetryPolicy(max_attempts=3),
        sleep=_no_sleep,
    )

    first = await lifecycle.create_or_resume(preflight, model_inputs={"mode": "automated"})
    second = await lifecycle.create_or_resume(preflight, model_inputs={"mode": "automated"})

    assert transport.create_calls == [mev.normalized_sequence]
    assert first.status.state == "paused"
    assert first.status.message_code == "swissmodel_create_outcome_unknown"
    assert second.status == first.status
    assert first.persisted_record() is not None
    assert_public_sinks_safe(first.public_result(), first.persisted_record())


@pytest.mark.asyncio
async def test_rate_limit_retry_honors_retry_after_and_resumes_the_known_job(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """429 retry guidance is capped and applied only to idempotent project reads."""
    _mev, preflight = _ready_preflight(monkeypatch)
    sleeps: list[float] = []

    async def record_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    transport = ScriptedOfficialTransport(
        create_outcomes=[_receipt()],
        project_outcomes=[
            SwissModelOperationError("rate_limited", retryable=True, retry_after_seconds=3.0),
            OfficialSwissModelProject(state="running", updated_at="2025-01-02T03:05:05+00:00"),
        ],
        summary_outcomes=[
            OfficialSwissModelModelSummary(
                model_id="model-7",
                model_url="https://swissmodel.expasy.org/project/project-42/model/model-7/pdb/",
                updated_at="2025-01-02T03:05:05+00:00",
            )
        ],
    )
    lifecycle = SwissModelResilientLifecycle(
        transport,
        policy=SwissModelRetryPolicy(
            max_attempts=3,
            initial_backoff_seconds=0.25,
            max_backoff_seconds=5.0,
        ),
        sleep=record_sleep,
    )

    queued = await lifecycle.create_or_resume(preflight)
    resumed = await lifecycle.create_or_resume(preflight)

    assert queued.status.state == "queued"
    assert resumed.status.state == "running"
    assert transport.create_calls == ["AVLG"]
    assert transport.project_calls == ["project-42", "project-42"]
    assert transport.summary_calls == ["project-42"]
    assert sleeps == [3.0]
    assert_public_sinks_safe(resumed.public_result(), resumed.persisted_record())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failure_kind", "message_code"),
    (
        ("http_error", "swissmodel_lifecycle_retry_exhausted"),
        ("timeout", "swissmodel_lifecycle_timeout"),
    ),
)
async def test_retry_budget_exhaustion_pauses_without_a_duplicate_create(
    monkeypatch: pytest.MonkeyPatch,
    failure_kind: str,
    message_code: str,
) -> None:
    """Transient HTTP failures/timeouts exhaust finite polling budgets safely."""
    _mev, preflight = _ready_preflight(monkeypatch)
    transport = ScriptedOfficialTransport(
        create_outcomes=[_receipt()],
        project_outcomes=[
            SwissModelOperationError(failure_kind, retryable=True),
            SwissModelOperationError(failure_kind, retryable=True),
            SwissModelOperationError(failure_kind, retryable=True),
        ],
    )
    lifecycle = SwissModelResilientLifecycle(
        transport,
        policy=SwissModelRetryPolicy(
            max_attempts=3,
            initial_backoff_seconds=0.1,
            max_backoff_seconds=0.2,
        ),
        sleep=_no_sleep,
    )

    await lifecycle.create_or_resume(preflight)
    paused = await lifecycle.create_or_resume(preflight)

    assert transport.create_calls == ["AVLG"]
    assert transport.project_calls == ["project-42", "project-42", "project-42"]
    assert paused.status.state == "paused"
    assert paused.status.message_code == message_code
    assert_public_sinks_safe(paused.public_result(), paused.persisted_record())


def test_exact_safe_cache_key_hits_only_for_normalized_identical_inputs() -> None:
    """Provider/method/fingerprint/model-input/selection differences cannot collide."""
    fingerprint = "a" * 64
    key = SwissModelLifecycleKey(
        mev_fingerprint=fingerprint,
        provider="SWISS-MODEL",
        method="AUTOMODEL",
        model_inputs=(("mode", "automated"), ("template", "template-1")),
        selection_id="selection-7",
    )
    same = SwissModelLifecycleKey(
        mev_fingerprint=fingerprint,
        provider="swissmodel",
        method="automodel",
        model_inputs=(("template", "template-1"), ("mode", "automated")),
        selection_id="selection-7",
    )
    different_template = SwissModelLifecycleKey(
        mev_fingerprint=fingerprint,
        provider="swissmodel",
        method="automodel",
        model_inputs=(("mode", "automated"), ("template", "template-2")),
        selection_id="selection-7",
    )
    record = SwissModelLifecycleRecord(
        key=key,
        status=SwissModelLifecycleStatus(
            state="queued",
            mev_fingerprint=fingerprint,
            method="automodel",
            selection_id="selection-7",
            request_id="project-42",
            submitted_at="2025-01-02T03:04:05+00:00",
            updated_at="2025-01-02T03:04:05+00:00",
        ),
        created_at="2025-01-02T03:04:05+00:00",
        updated_at="2025-01-02T03:04:05+00:00",
    )
    store = SwissModelLifecycleStore()
    store.put(record)

    assert key.cache_key == same.cache_key
    assert store.get(same) == record
    assert different_template.cache_key != key.cache_key
    assert store.get(different_template) is None
    assert_public_sinks_safe(record.to_persisted())


@pytest.mark.asyncio
async def test_restart_restores_only_the_exact_safe_record_and_pauses_until_pdb_qualification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A resumed provider completion cannot become success without exact PDB qualification."""
    _mev, preflight = _ready_preflight(monkeypatch)
    first_transport = ScriptedOfficialTransport(create_outcomes=[_receipt()])
    first = SwissModelResilientLifecycle(first_transport, sleep=_no_sleep)
    queued = await first.create_or_resume(preflight, model_inputs={"mode": "automated"})
    persisted = queued.persisted_record()
    assert persisted is not None

    resumed_transport = ScriptedOfficialTransport(
        create_outcomes=[],
        project_outcomes=[OfficialSwissModelProject(state="succeeded")],
        summary_outcomes=[
            OfficialSwissModelModelSummary(
                model_id="model-7",
                model_url="https://swissmodel.expasy.org/project/project-42/model/model-7/pdb/",
            )
        ],
    )
    restarted = SwissModelResilientLifecycle(resumed_transport, sleep=_no_sleep)
    completed = await restarted.create_or_resume(
        preflight,
        model_inputs={"mode": "automated"},
        persisted_record=persisted,
    )

    assert first_transport.create_calls == ["AVLG"]
    assert resumed_transport.create_calls == []
    assert resumed_transport.project_calls == ["project-42"]
    assert resumed_transport.summary_calls == ["project-42"]
    assert completed.status.state == "paused"
    assert completed.status.message_code == "swissmodel_pdb_qualification_required"
    assert completed.qualified_pdb is None
    assert completed.public_result()["requestId"] == "project-42"
    assert_public_sinks_safe(persisted, completed.public_result(), completed.persisted_record())


class _ReceiptDecoder:
    """Small reviewed-decoder stand-in; the raw JSON never leaves transport scope."""

    def automodel_receipt(self, _payload: object) -> OfficialAutomodelReceipt:
        return _receipt()

    def project(self, _payload: object) -> OfficialSwissModelProject:
        return OfficialSwissModelProject(state="queued")

    def project_model_summary(self, _payload: object) -> OfficialSwissModelModelSummary:
        return OfficialSwissModelModelSummary()


@pytest.mark.asyncio
async def test_http_transport_uses_json_and_canonical_official_routes_with_finite_timeouts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The local transport uses only JSON and reviewed singular CoreAPI routes."""
    monkeypatch.setenv("SWISSMODEL_API_TOKEN", secrets.token_urlsafe(32))
    observed: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        observed["method"] = request.method
        observed["url"] = str(request.url)
        observed["hasTokenAuthorization"] = request.headers.get("Authorization", "").startswith("Token ")
        observed["contentType"] = request.headers.get("Content-Type")
        observed["json"] = json.loads(request.content)
        return httpx.Response(200, json={"ignored": True})

    routes = OfficialSwissModelCoreApiRoutes(
        automodel_url="https://swissmodel.expasy.org/automodel/",
        project_url=lambda project_id: f"https://swissmodel.expasy.org/project/{project_id}/",
        project_models_summary_url=(
            lambda project_id: f"https://swissmodel.expasy.org/project/{project_id}/models/"
        ),
        model_pdb_url=(
            lambda project_id, model_id:
            f"https://swissmodel.expasy.org/project/{project_id}/model/{model_id}/pdb/"
        ),
    )
    policy = SwissModelRetryPolicy(
        connect_timeout_seconds=1.0,
        read_timeout_seconds=2.0,
        overall_timeout_seconds=3.0,
    )
    transport = OfficialSwissModelCoreApiHttpTransport(
        routes=routes,
        decoder=_ReceiptDecoder(),
        policy=policy,
        http_transport=httpx.MockTransport(handler),
    )

    receipt = await transport.create_automodel(target_sequences="AVLG")

    assert receipt.request_id == "project-42"
    assert observed["method"] == "POST"
    assert observed["url"] == "https://swissmodel.expasy.org/automodel/"
    assert observed["hasTokenAuthorization"] is True
    assert observed["contentType"] == "application/json"
    assert observed["json"] == {"target_sequences": "AVLG"}
    assert "Authorization" not in transport.__dict__
    assert policy.httpx_timeout().connect == 1.0
    assert policy.httpx_timeout().read == 2.0
    assert_public_sinks_safe({"receipt": receipt.__dict__, "transportFields": list(transport.__dict__)})


@settings(max_examples=25)
@given(
    mode=st.text(alphabet="abcdefghijklmnopqrstuvwxyz0123456789", min_size=1, max_size=12),
    changed_mode=st.text(alphabet="abcdefghijklmnopqrstuvwxyz0123456789", min_size=1, max_size=12),
)
def test_safe_lifecycle_keys_are_deterministic_and_exact(
    mode: str,
    changed_mode: str,
) -> None:
    """**Validates: Requirements 1.15, 2.16, 3.12, 3.14**

    Canonicalized ordering yields a stable key, while a changed normalized
    model-input discriminator cannot reuse the same lifecycle record.
    """
    assume(mode != changed_mode)
    fingerprint = "b" * 64
    first = SwissModelLifecycleKey(
        mev_fingerprint=fingerprint,
        provider="swissmodel",
        method="automodel",
        model_inputs=(("mode", mode), ("template", "template-1")),
    )
    same = SwissModelLifecycleKey(
        mev_fingerprint=fingerprint,
        provider="SWISS-MODEL",
        method="AUTOMODEL",
        model_inputs=(("template", "template-1"), ("mode", mode)),
    )
    changed = SwissModelLifecycleKey(
        mev_fingerprint=fingerprint,
        provider="swissmodel",
        method="automodel",
        model_inputs=(("mode", changed_mode), ("template", "template-1")),
    )

    assert first.cache_key == same.cache_key
    assert first.cache_key != changed.cache_key
