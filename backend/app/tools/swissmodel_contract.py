"""Contract-bound SWISS-MODEL direct-submission admission helpers.

The official `SWISS-MODEL CoreAPI <https://swissmodel.expasy.org/coreapi/>`_
reviewed for Task 20.3 documents direct automated modelling through the
``automodel`` create operation.  Its documented required payload member is
``target_sequences``; this module therefore represents no guessed discovery
endpoint, template identifier, request field, selection threshold, or result
schema.

Authentication and the create/poll/cancel/download HTTP lifecycle intentionally
remain outside this module until Task 20.4.  The injectable transport below
lets the exact admission boundary be tested without a live request or a
credential.  It receives only the exact normalized MEV sequence.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import re
from typing import Literal, Protocol
from urllib.parse import urlparse

from .swissmodel_runtime import (
    SWISSMODEL_PROVIDER,
    SwissModelPreflight,
    mev_sequence_fingerprint,
    normalize_mev_sequence,
)


# Official CoreAPI operation terminology, retained as provenance rather than a
# locally invented provider method.  No endpoint is contacted in this task.
OFFICIAL_AUTOMODEL_METHOD = "automodel"

_SAFE_OPAQUE_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]*\Z")
_OFFICIAL_COREAPI_HOST = "swissmodel.expasy.org"


@dataclass(frozen=True)
class OfficialSwissModelContract:
    """Capabilities established by a reviewed official contract.

    ``direct_sequence_submission`` is true only for the documented CoreAPI
    ``automodel`` create operation.  Discovery remains abstract so a future
    adapter can expose it only after independently validating a documented
    discovery response and its selection semantics.
    """

    direct_sequence_submission: bool
    direct_submission_method: str | None = None


OFFICIAL_AUTOMODEL_DIRECT_CONTRACT = OfficialSwissModelContract(
    direct_sequence_submission=True,
    direct_submission_method=OFFICIAL_AUTOMODEL_METHOD,
)


@dataclass(frozen=True)
class OfficialDiscoverySelection:
    """A provider-validated selection, normalized by a future discovery adapter.

    This is deliberately an operation-neutral internal record, not a claim
    about any SWISS-MODEL response field or endpoint.  A future documented
    discovery adapter may construct it only after the provider explicitly
    marks the selection submission-eligible and binds it to the exact MEV
    fingerprint.
    """

    selection_id: str
    mev_fingerprint: str
    submission_eligible: bool
    method: str


@dataclass(frozen=True)
class SwissModelSubmissionAdmission:
    """Private admission state with a deliberately limited public projection."""

    admitted: bool
    status: Literal["ready", "paused"]
    message_code: str | None = None
    action: str | None = None
    normalized_sequence: str | None = None
    mev_fingerprint: str | None = None
    method: str | None = None
    selection_id: str | None = None

    def public_result(self) -> dict[str, str]:
        """Return safe status/provenance without the sequence or raw response."""
        result = {"status": self.status, "provider": SWISSMODEL_PROVIDER}
        if self.message_code:
            result["messageCode"] = self.message_code
        if self.action:
            result["action"] = self.action
        if self.mev_fingerprint:
            result["mevFingerprint"] = self.mev_fingerprint
        safe_method = _safe_opaque_identifier(self.method)
        if safe_method:
            result["method"] = safe_method
        safe_selection_id = _safe_opaque_identifier(self.selection_id)
        if safe_selection_id:
            result["selectionId"] = safe_selection_id
        return result


def _safe_opaque_identifier(value: object) -> str | None:
    """Return an opaque identifier only when safe to persist or serialize."""
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    return candidate if _SAFE_OPAQUE_IDENTIFIER.fullmatch(candidate) else None


def _paused_admission(
    message_code: str,
    action: str,
) -> SwissModelSubmissionAdmission:
    return SwissModelSubmissionAdmission(
        admitted=False,
        status="paused",
        message_code=message_code,
        action=action,
    )


def admit_swissmodel_submission(
    preflight: SwissModelPreflight,
    *,
    contract: OfficialSwissModelContract = OFFICIAL_AUTOMODEL_DIRECT_CONTRACT,
    discovery_selection: OfficialDiscoverySelection | None = None,
) -> SwissModelSubmissionAdmission:
    """Enforce the exact documented submission-admission predicate.

    The preflight validates the completed/current Step 9-2 relationship.  It
    is checked again here against the private normalized sequence so a caller
    cannot turn a stale or tampered preflight result into a provider request.
    Direct admission is available only through the reviewed ``automodel``
    contract.  If direct submission is unavailable, a selection can qualify
    only when an official discovery adapter explicitly marked it eligible and
    bound it to the same fingerprint.
    """
    if not preflight.ready or preflight.status != "ready":
        return _paused_admission(
            preflight.message_code or "swissmodel_preflight_not_ready",
            preflight.action or "Complete SWISS-MODEL preflight before retrying Step 11-2.",
        )

    normalized_sequence = normalize_mev_sequence(preflight.normalized_sequence)
    if normalized_sequence is None:
        return _paused_admission(
            "swissmodel_submission_sequence_invalid",
            "Re-run MEV assembly and retry Step 11-2 with the exact completed construct.",
        )

    expected_fingerprint = mev_sequence_fingerprint(normalized_sequence)
    if preflight.mev_fingerprint != expected_fingerprint:
        return _paused_admission(
            "swissmodel_mev_fingerprint_mismatch",
            "Re-run MEV assembly and retry Step 11-2 with the exact current construct.",
        )

    direct_method = _safe_opaque_identifier(contract.direct_submission_method)
    if contract.direct_sequence_submission and direct_method:
        return SwissModelSubmissionAdmission(
            admitted=True,
            status="ready",
            normalized_sequence=normalized_sequence,
            mev_fingerprint=expected_fingerprint,
            method=direct_method,
        )

    if discovery_selection is None:
        return _paused_admission(
            "swissmodel_submission_selection_unavailable",
            "Wait for an official provider-valid submission selection, then retry Step 11-2.",
        )

    if not discovery_selection.submission_eligible:
        return _paused_admission(
            "swissmodel_submission_selection_invalid",
            "Choose an explicit provider-valid submission selection, then retry Step 11-2.",
        )

    if discovery_selection.mev_fingerprint != expected_fingerprint:
        return _paused_admission(
            "swissmodel_submission_selection_mismatch",
            "Re-run official discovery for the exact current MEV, then retry Step 11-2.",
        )

    selection_id = _safe_opaque_identifier(discovery_selection.selection_id)
    method = _safe_opaque_identifier(discovery_selection.method)
    if not selection_id or not method:
        return _paused_admission(
            "swissmodel_submission_selection_invalid",
            "Choose an explicit provider-valid submission selection, then retry Step 11-2.",
        )

    return SwissModelSubmissionAdmission(
        admitted=True,
        status="ready",
        normalized_sequence=normalized_sequence,
        mev_fingerprint=expected_fingerprint,
        method=method,
        selection_id=selection_id,
    )


@dataclass(frozen=True)
class OfficialAutomodelReceipt:
    """Only provider values permitted to cross the direct-submit boundary."""

    request_id: str | None = None
    model_id: str | None = None
    model_url: str | None = None
    submitted_at: datetime | None = None


class OfficialAutomodelTransport(Protocol):
    """The documented direct ``automodel`` operation, without auth details."""

    async def create_automodel(
        self,
        *,
        target_sequences: str,
    ) -> OfficialAutomodelReceipt:
        """Submit the documented ``target_sequences`` payload member."""


def _safe_public_https_url(value: object) -> str | None:
    """Retain only a query-free official-host HTTPS URL in provenance."""
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    try:
        parsed = urlparse(candidate)
        port = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme != "https"
        or parsed.hostname != _OFFICIAL_COREAPI_HOST
        or port not in {None, 443}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        return None
    return candidate


def _safe_timestamp(value: datetime | None) -> str:
    """Normalize a provider timestamp or create a safe local submission time."""
    timestamp = value or datetime.now(timezone.utc)
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    return timestamp.astimezone(timezone.utc).isoformat()


@dataclass(frozen=True)
class SwissModelAutomodelSubmission:
    """A direct submission with a safe, public provenance projection."""

    admission: SwissModelSubmissionAdmission
    request_id: str | None
    model_id: str | None
    model_url: str | None
    submitted_at: str

    def public_provenance(self) -> dict[str, str]:
        """Serialize only the approved public-safe provenance fields."""
        result = {
            "status": "queued",
            "provider": SWISSMODEL_PROVIDER,
            "method": self.admission.method or OFFICIAL_AUTOMODEL_METHOD,
            "mevFingerprint": self.admission.mev_fingerprint or "",
            "submittedAt": self.submitted_at,
        }
        safe_request_id = _safe_opaque_identifier(self.request_id)
        if safe_request_id:
            result["requestId"] = safe_request_id
        safe_model_id = _safe_opaque_identifier(self.model_id)
        if safe_model_id:
            result["modelId"] = safe_model_id
        safe_model_url = _safe_public_https_url(self.model_url)
        if safe_model_url:
            result["modelUrl"] = safe_model_url
        safe_selection_id = _safe_opaque_identifier(self.admission.selection_id)
        if safe_selection_id:
            result["selectionId"] = safe_selection_id
        return result


class SwissModelAutomodelAdapter:
    """Submit only an exact MEV admitted by the direct official contract."""

    def __init__(
        self,
        transport: OfficialAutomodelTransport,
        *,
        contract: OfficialSwissModelContract = OFFICIAL_AUTOMODEL_DIRECT_CONTRACT,
    ):
        self._transport = transport
        self._contract = contract

    async def submit(
        self,
        preflight: SwissModelPreflight,
    ) -> SwissModelAutomodelSubmission:
        """Recheck preflight, then transmit only the exact normalized MEV."""
        admission = admit_swissmodel_submission(preflight, contract=self._contract)
        normalized_sequence = normalize_mev_sequence(admission.normalized_sequence)
        if (
            not admission.admitted
            or admission.method != OFFICIAL_AUTOMODEL_METHOD
            or normalized_sequence is None
            or admission.mev_fingerprint
            != mev_sequence_fingerprint(normalized_sequence)
        ):
            raise ValueError("SWISS-MODEL direct submission requires an exact admitted MEV")

        receipt = await self._transport.create_automodel(
            target_sequences=normalized_sequence,
        )
        return SwissModelAutomodelSubmission(
            admission=admission,
            request_id=receipt.request_id,
            model_id=receipt.model_id,
            model_url=receipt.model_url,
            submitted_at=_safe_timestamp(receipt.submitted_at),
        )
