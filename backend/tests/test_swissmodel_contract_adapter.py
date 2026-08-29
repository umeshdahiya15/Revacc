"""Task 20.3 tests for the documented SWISS-MODEL admission boundary.

The fake transport models only the official CoreAPI direct ``automodel``
operation and receives no credential.  No test makes a network request.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import secrets

import pytest

from app.tools.swissmodel_contract import (
    OFFICIAL_AUTOMODEL_DIRECT_CONTRACT,
    OfficialAutomodelReceipt,
    OfficialDiscoverySelection,
    OfficialSwissModelContract,
    SwissModelAutomodelAdapter,
    admit_swissmodel_submission,
)
from app.tools.swissmodel_runtime import (
    mev_sequence_fingerprint,
    preflight_swissmodel_submission,
)

from .swissmodel_contract_fixtures import assert_public_sinks_safe, completed_step_9_2_mev


class RecordingAutomodelTransport:
    """A local direct-operation fake that records the one documented payload."""

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


@pytest.mark.asyncio
async def test_documented_direct_submission_transmits_only_the_exact_mev_and_safe_provenance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The documented automodel path receives only the normalized Step-9-2 MEV."""
    mev, preflight = _ready_preflight(monkeypatch)
    admission = admit_swissmodel_submission(
        preflight,
        contract=OFFICIAL_AUTOMODEL_DIRECT_CONTRACT,
    )
    transport = RecordingAutomodelTransport(
        OfficialAutomodelReceipt(
            request_id="request-42",
            model_id="model-7",
            model_url="https://swissmodel.expasy.org/project/request-42/model/model-7/pdb/",
            submitted_at=datetime(2025, 1, 2, 3, 4, 5, tzinfo=timezone.utc),
        )
    )

    submission = await SwissModelAutomodelAdapter(transport).submit(preflight)
    public = submission.public_provenance()

    assert admission.admitted is True
    assert submission.admission == admission
    assert transport.target_sequences == [mev.normalized_sequence]
    assert public == {
        "status": "queued",
        "provider": "swissmodel",
        "method": "automodel",
        "mevFingerprint": mev.fingerprint,
        "submittedAt": "2025-01-02T03:04:05+00:00",
        "requestId": "request-42",
        "modelId": "model-7",
        "modelUrl": "https://swissmodel.expasy.org/project/request-42/model/model-7/pdb/",
    }
    assert "sequence" not in public
    assert_public_sinks_safe(public)


def test_explicit_provider_valid_discovery_selection_is_admitted_when_direct_submission_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A future discovery adapter must bind its explicit selection to this MEV."""
    mev, preflight = _ready_preflight(monkeypatch)
    discovery_only_contract = OfficialSwissModelContract(
        direct_sequence_submission=False,
    )
    selection = OfficialDiscoverySelection(
        selection_id="selection-17",
        mev_fingerprint=mev.fingerprint,
        submission_eligible=True,
        method="provider-discovery",
    )

    admission = admit_swissmodel_submission(
        preflight,
        contract=discovery_only_contract,
        discovery_selection=selection,
    )

    assert admission.admitted is True
    assert admission.method == "provider-discovery"
    assert admission.selection_id == "selection-17"
    assert admission.public_result() == {
        "status": "ready",
        "provider": "swissmodel",
        "mevFingerprint": mev.fingerprint,
        "method": "provider-discovery",
        "selectionId": "selection-17",
    }
    assert_public_sinks_safe(admission.public_result())


@pytest.mark.parametrize(
    "selection",
    (
        None,
        OfficialDiscoverySelection(
            selection_id="selection-17",
            mev_fingerprint="0" * 64,
            submission_eligible=False,
            method="provider-discovery",
        ),
        OfficialDiscoverySelection(
            selection_id="selection-17",
            mev_fingerprint="0" * 64,
            submission_eligible=True,
            method="provider-discovery",
        ),
    ),
)
def test_absent_or_invalid_discovery_selection_pauses_before_submission(
    monkeypatch: pytest.MonkeyPatch,
    selection: OfficialDiscoverySelection | None,
) -> None:
    """No fallback selection, template, threshold, or transport call is invented."""
    _mev, preflight = _ready_preflight(monkeypatch)
    admission = admit_swissmodel_submission(
        preflight,
        contract=OfficialSwissModelContract(direct_sequence_submission=False),
        discovery_selection=selection,
    )

    assert admission.admitted is False
    assert admission.status == "paused"
    assert admission.message_code in {
        "swissmodel_submission_selection_unavailable",
        "swissmodel_submission_selection_invalid",
        "swissmodel_submission_selection_mismatch",
    }
    assert_public_sinks_safe(admission.public_result())


@pytest.mark.asyncio
async def test_changed_mev_fingerprint_pauses_even_if_a_preflight_object_is_tampered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The admission gate recomputes the fingerprint before any transport call."""
    _mev, preflight = _ready_preflight(monkeypatch)
    changed_fingerprint = mev_sequence_fingerprint("AVLA")
    tampered_preflight = replace(preflight, mev_fingerprint=changed_fingerprint)

    admission = admit_swissmodel_submission(tampered_preflight)

    transport = RecordingAutomodelTransport(OfficialAutomodelReceipt())
    with pytest.raises(ValueError, match="exact admitted MEV"):
        await SwissModelAutomodelAdapter(transport).submit(tampered_preflight)

    assert admission.admitted is False
    assert admission.status == "paused"
    assert admission.message_code == "swissmodel_mev_fingerprint_mismatch"
    assert transport.target_sequences == []
    assert_public_sinks_safe(admission.public_result())


@pytest.mark.asyncio
async def test_public_provenance_redacts_credential_bearing_model_urls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only safe IDs, timestamps, and a credential-free HTTPS URL can serialize."""
    _mev, preflight = _ready_preflight(monkeypatch)
    admission = admit_swissmodel_submission(preflight)
    transport = RecordingAutomodelTransport(
        OfficialAutomodelReceipt(
            request_id="request-42",
            model_id="model-7",
            model_url="https://swissmodel.expasy.org/project/request-42/model/model-7/pdb/?token=fixture",
        )
    )

    public = (await SwissModelAutomodelAdapter(transport).submit(preflight)).public_provenance()

    assert public["requestId"] == "request-42"
    assert public["modelId"] == "model-7"
    assert "modelUrl" not in public
    assert "token" not in str(public).lower()
    assert_public_sinks_safe(public)
