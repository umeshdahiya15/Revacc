"""Safe SWISS-MODEL lifecycle capability boundary.

The reviewed official `SWISS-MODEL CoreAPI <https://swissmodel.expasy.org/coreapi/>`_
documents the direct ``automodel`` create operation, but its rendered schema
does not document how a previously issued token is used for authenticated
requests, how a create response binds to a project/status result, or a
cancellation operation.  The project/model read operations require identifiers
whose documented relationship to ``automodel`` creation is likewise absent.

Consequently this module deliberately has no HTTP endpoint, header, response
parser, or credential handling.  It exposes a typed, public-safe lifecycle
record and a no-I/O capability boundary that pauses rather than guessing
create/poll/cancel/result semantics.  A later reviewed contract can supply
those operations without changing the public redaction boundary below.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
import re
from typing import Literal
from urllib.parse import urlparse

from .swissmodel_contract import (
    SwissModelAutomodelAdapter,
    SwissModelSubmissionAdmission,
    admit_swissmodel_submission,
)
from .swissmodel_runtime import SWISSMODEL_PROVIDER, SwissModelPreflight


SwissModelLifecycleState = Literal[
    "queued", "running", "paused", "failed", "succeeded"
]

_VALID_STATES = frozenset({"queued", "running", "paused", "failed", "succeeded"})
_NON_TERMINAL_RECORDED_STATES = frozenset({"queued", "running"})
_SAFE_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]*\Z")
_SAFE_FINGERPRINT = re.compile(r"[0-9a-f]{64}\Z")
_OFFICIAL_COREAPI_HOST = "swissmodel.expasy.org"


@dataclass(frozen=True)
class _LifecycleMessage:
    """A fixed public-safe reason/action pair; never provider response text."""

    reason: str
    action: str


_LIFECYCLE_MESSAGES: dict[str, _LifecycleMessage] = {
    "swissmodel_official_lifecycle_unavailable": _LifecycleMessage(
        reason=(
            "Official SWISS-MODEL direct-sequence admission passed, but no authenticated "
            "official-contract client lifecycle is available to submit the MEV."
        ),
        action=(
            "Attach a validated external PDB/HTTPS model, or wait for the authenticated "
            "official lifecycle before retrying Step 11-2."
        ),
    ),
    "swissmodel_create_outcome_unknown": _LifecycleMessage(
        reason=(
            "The official create request ended before its acceptance could be verified; "
            "the system did not repeat the request."
        ),
        action=(
            "Do not resubmit automatically. Verify the provider job manually or attach a validated external model."
        ),
    ),
    "swissmodel_create_request_id_missing": _LifecycleMessage(
        reason=(
            "The official create response did not contain a safe recorded job identifier, "
            "so the job cannot be resumed safely."
        ),
        action=(
            "Verify the provider job manually before retrying, or attach a validated external model."
        ),
    ),
    "swissmodel_lifecycle_rate_limited": _LifecycleMessage(
        reason="The official lifecycle remained rate-limited after the bounded retry budget.",
        action="Wait for the provider retry interval, then resume the recorded job without resubmitting it.",
    ),
    "swissmodel_lifecycle_timeout": _LifecycleMessage(
        reason="The official lifecycle timed out within its bounded deadline.",
        action="Resume the recorded job later without creating a duplicate submission.",
    ),
    "swissmodel_lifecycle_retry_exhausted": _LifecycleMessage(
        reason="The official lifecycle remained unavailable after its bounded retry budget.",
        action="Resume the recorded job later or attach a validated external model.",
    ),
    "swissmodel_lifecycle_record_invalid": _LifecycleMessage(
        reason="The recorded official lifecycle metadata is not safe or does not match the exact MEV input.",
        action="Do not resubmit automatically; verify the provider job or attach a validated external model.",
    ),
    "swissmodel_submission_not_admitted": _LifecycleMessage(
        reason="Official SWISS-MODEL submission is not admissible for the exact current MEV.",
        action="Review the official submission prerequisites and retry Step 11-2.",
    ),
    "swissmodel_polling_unavailable": _LifecycleMessage(
        reason=(
            "The reviewed official contract does not expose a safely bindable model-status "
            "poll operation for this recorded job."
        ),
        action="Keep the job paused and use a validated external model until lifecycle support is documented.",
    ),
    "swissmodel_model_result_unavailable": _LifecycleMessage(
        reason=(
            "The reviewed official contract does not expose a provider-approved model-result "
            "operation bound to this recorded job."
        ),
        action="Attach a validated external PDB/HTTPS model until a documented result contract is available.",
    ),
    "swissmodel_pdb_qualification_required": _LifecycleMessage(
        reason=(
            "The provider reported a model result, but no guarded exact-PDB qualification "
            "operation is configured for this recorded job."
        ),
        action="Attach a validated external PDB model or configure the reviewed guarded PDB operation before retrying.",
    ),
    "swissmodel_pdb_qualification_failed": _LifecycleMessage(
        reason=(
            "The provider model could not pass guarded download and exact assembled-MEV "
            "coordinate qualification."
        ),
        action="Use a complete exact PDB model or retry the recorded provider job; no structure metrics were created.",
    ),
    "swissmodel_cancellation_unavailable": _LifecycleMessage(
        reason=(
            "The reviewed official contract does not expose a cancellation operation for this "
            "recorded official job."
        ),
        action="Keep the job paused and contact the provider manually if cancellation is required.",
    ),
    "swissmodel_cancellation_not_available": _LifecycleMessage(
        reason="Cancellation is available only for a recorded non-terminal official job.",
        action="Select a queued or running recorded official job before requesting cancellation.",
    ),
    "swissmodel_provider_terminal_failure": _LifecycleMessage(
        reason="The provider reported a terminal model failure through a verified lifecycle adapter.",
        action="Review the provider job and attach a validated external model before retrying Step 11-2.",
    ),
    "swissmodel_lifecycle_transition_invalid": _LifecycleMessage(
        reason="The requested official lifecycle transition is not valid for the recorded job state.",
        action="Refresh the recorded official job status before taking another lifecycle action.",
    ),
    "swissmodel_lifecycle_status_unavailable": _LifecycleMessage(
        reason="The official lifecycle status is unavailable.",
        action="Retry only after documented official lifecycle support is available.",
    ),
}


@dataclass(frozen=True)
class OfficialSwissModelLifecycleCapability:
    """Documented lifecycle operations currently usable by this backend.

    ``automodel_create`` is documented, but ``authenticated_create`` is false:
    the reviewed schema does not document how the server's already-configured
    token may authenticate that request.  ``poll``, ``cancel``, and
    ``model_result`` remain false because their required create-result binding
    or operation semantics are not documented.
    """

    automodel_create: bool
    authenticated_create: bool
    poll: bool
    cancel: bool
    model_result: bool

    @property
    def complete(self) -> bool:
        """Whether every required lifecycle operation is safely documented."""
        return all((
            self.automodel_create,
            self.authenticated_create,
            self.poll,
            self.cancel,
            self.model_result,
        ))


OFFICIAL_COREAPI_LIFECYCLE_CAPABILITY = OfficialSwissModelLifecycleCapability(
    automodel_create=True,
    authenticated_create=False,
    poll=False,
    cancel=False,
    model_result=False,
)


def _safe_identifier(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    return candidate if _SAFE_IDENTIFIER.fullmatch(candidate) else None


def _safe_fingerprint(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    candidate = value.strip().lower()
    return candidate if _SAFE_FINGERPRINT.fullmatch(candidate) else None


def _safe_https_url(value: object) -> str | None:
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


def _safe_timestamp(value: datetime | str | None) -> str | None:
    """Canonicalize a timestamp, rejecting arbitrary provider strings."""
    if isinstance(value, datetime):
        timestamp = value
    elif isinstance(value, str):
        try:
            timestamp = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    return timestamp.astimezone(timezone.utc).isoformat()


def _now_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _known_message_code(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, str) and value in _LIFECYCLE_MESSAGES:
        return value
    return "swissmodel_lifecycle_status_unavailable"


def _safe_state(value: object) -> SwissModelLifecycleState:
    if isinstance(value, str) and value in _VALID_STATES:
        return value  # type: ignore[return-value]
    return "paused"


@dataclass(frozen=True)
class SwissModelLifecycleStatus:
    """A safe lifecycle projection with no request body, credential, or raw error.

    ``succeeded`` here describes only a verified provider lifecycle state.  It
    never marks pipeline step 11-2 successful: guarded download and exact
    coordinate qualification remain a separate later boundary.
    """

    state: SwissModelLifecycleState
    message_code: str | None = None
    mev_fingerprint: str | None = None
    method: str | None = None
    selection_id: str | None = None
    request_id: str | None = None
    model_id: str | None = None
    model_url: str | None = None
    pdb_qualified: bool = False
    submitted_at: str | None = None
    updated_at: str | None = None
    completed_at: str | None = None

    @classmethod
    def paused_for_admission(
        cls,
        admission: SwissModelSubmissionAdmission,
        *,
        message_code: str = "swissmodel_official_lifecycle_unavailable",
    ) -> "SwissModelLifecycleStatus":
        """Build a safe pause from approved admission metadata only."""
        return cls(
            state="paused",
            message_code=_known_message_code(message_code),
            mev_fingerprint=_safe_fingerprint(admission.mev_fingerprint),
            method=_safe_identifier(admission.method),
            selection_id=_safe_identifier(admission.selection_id),
            updated_at=_now_timestamp(),
        )

    @property
    def reason(self) -> str:
        """Return only a fixed reason selected by a safe message code."""
        code = _known_message_code(self.message_code)
        if code is None:
            return _LIFECYCLE_MESSAGES["swissmodel_lifecycle_status_unavailable"].reason
        return _LIFECYCLE_MESSAGES[code].reason

    @property
    def action(self) -> str:
        """Return only a fixed user action selected by a safe message code."""
        code = _known_message_code(self.message_code)
        if code is None:
            return _LIFECYCLE_MESSAGES["swissmodel_lifecycle_status_unavailable"].action
        return _LIFECYCLE_MESSAGES[code].action

    @property
    def is_recorded_non_terminal_job(self) -> bool:
        """Cancellation/polling require a safe request ID and non-terminal state."""
        return (
            _safe_state(self.state) in _NON_TERMINAL_RECORDED_STATES
            and _safe_identifier(self.request_id) is not None
        )

    def with_message(
        self,
        message_code: str,
        *,
        at: datetime | str | None = None,
    ) -> "SwissModelLifecycleStatus":
        """Attach a known safe status code without accepting provider prose."""
        return replace(
            self,
            message_code=_known_message_code(message_code),
            updated_at=_safe_timestamp(at) or _now_timestamp(),
        )

    def public_result(self) -> dict[str, object]:
        """Serialize only public-safe lifecycle state and provenance."""
        result = {
            "status": _safe_state(self.state),
            "provider": SWISSMODEL_PROVIDER,
        }
        code = _known_message_code(self.message_code)
        if code:
            result["messageCode"] = code
            result["action"] = _LIFECYCLE_MESSAGES[code].action
        for key, value, normalizer in (
            ("mevFingerprint", self.mev_fingerprint, _safe_fingerprint),
            ("method", self.method, _safe_identifier),
            ("selectionId", self.selection_id, _safe_identifier),
            ("requestId", self.request_id, _safe_identifier),
            ("modelId", self.model_id, _safe_identifier),
            ("modelUrl", self.model_url, _safe_https_url),
            ("submittedAt", self.submitted_at, _safe_timestamp),
            ("updatedAt", self.updated_at, _safe_timestamp),
            ("completedAt", self.completed_at, _safe_timestamp),
        ):
            safe_value = normalizer(value)
            if safe_value:
                result[key] = safe_value
        if self.pdb_qualified is True:
            result["pdbQualified"] = True
        return result


def public_swissmodel_lifecycle_status(value: object) -> dict[str, object]:
    """Return an allowlisted API/UI projection of an official lifecycle value.

    Public callers may receive persisted lifecycle metadata, so this function
    never trustfully forwards stored provider data.  It reconstructs a status
    from only the established non-secret fields, derives all prose from the
    fixed message map, and drops any raw body, header, credential, stack trace,
    or unrecognised URL.  Malformed input becomes a safe actionable pause.
    """
    fallback = SwissModelLifecycleStatus(
        state="paused",
        message_code="swissmodel_lifecycle_status_unavailable",
        updated_at=_now_timestamp(),
    )
    if isinstance(value, SwissModelLifecycleStatus):
        status = value
    elif isinstance(value, dict):
        state = value.get("status")
        if (
            value.get("provider") != SWISSMODEL_PROVIDER
            or not isinstance(state, str)
            or state not in _VALID_STATES
        ):
            status = fallback
        else:
            status = SwissModelLifecycleStatus(
                state=_safe_state(state),
                message_code=_known_message_code(value.get("messageCode")),
                mev_fingerprint=_safe_fingerprint(value.get("mevFingerprint")),
                method=_safe_identifier(value.get("method")),
                selection_id=_safe_identifier(value.get("selectionId")),
                request_id=_safe_identifier(value.get("requestId")),
                model_id=_safe_identifier(value.get("modelId")),
                model_url=_safe_https_url(value.get("modelUrl")),
                pdb_qualified=value.get("pdbQualified") is True,
                submitted_at=_safe_timestamp(value.get("submittedAt")),
                updated_at=_safe_timestamp(value.get("updatedAt")),
                completed_at=_safe_timestamp(value.get("completedAt")),
            )
    else:
        status = fallback

    result = status.public_result()
    state = _safe_state(result.get("status"))
    generic_messages = {
        "queued": (
            "The official SWISS-MODEL job is queued for provider processing.",
            "Refresh this run to see provider progress.",
        ),
        "running": (
            "The official SWISS-MODEL job is being processed by the provider.",
            "Refresh this run to see provider progress.",
        ),
        "paused": (
            "The official SWISS-MODEL job is paused and needs review.",
            "Review the status or attach a validated external PDB model.",
        ),
        "failed": (
            "The official SWISS-MODEL job ended without a validated model.",
            "Review the status or attach a validated external PDB model before retrying.",
        ),
        "succeeded": (
            "The official SWISS-MODEL job completed with validated lifecycle status.",
            "Review the qualified structure result before continuing downstream analysis.",
        ),
    }
    code = _known_message_code(status.message_code)
    if code is not None:
        result["message"] = _LIFECYCLE_MESSAGES[code].reason
    else:
        message, action = generic_messages[state]
        result["message"] = message
        result["action"] = action
    return result


def apply_verified_lifecycle_state(
    status: SwissModelLifecycleStatus,
    *,
    state: SwissModelLifecycleState,
    at: datetime | str | None = None,
) -> SwissModelLifecycleStatus:
    """Apply a state already validated by a future documented response parser.

    This function intentionally accepts no provider response, message, header,
    or arbitrary ID.  It is a public-state reducer only; a future official
    transport/parser must first establish the verified state using documented
    semantics.
    """
    requested_state = _safe_state(state)
    current_state = _safe_state(status.state)
    if (
        requested_state != state
        or current_state in {"failed", "succeeded"}
        or current_state == "paused"
    ):
        return status.with_message("swissmodel_lifecycle_transition_invalid", at=at)

    message_code = (
        "swissmodel_provider_terminal_failure"
        if requested_state == "failed"
        else None
    )
    timestamp = _safe_timestamp(at) or _now_timestamp()
    return replace(
        status,
        state=requested_state,
        message_code=message_code,
        updated_at=timestamp,
        completed_at=timestamp if requested_state in {"failed", "succeeded"} else None,
    )


class SwissModelLifecycleAdapter:
    """No-I/O lifecycle boundary for the currently documented contract.

    It runs the approved exact-sequence admission gate, then pauses.  It never
    calls the optional approved submission adapter, because doing so would
    require undocumented authentication and result-lifecycle behavior.  The
    same no-guess rule applies to polling, cancellation, and result acquisition.
    """

    def __init__(self, submission_adapter: SwissModelAutomodelAdapter | None = None):
        # Retained only for a future documented lifecycle implementation. Until
        # then it is intentionally never called, including for an admitted MEV.
        self._submission_adapter = submission_adapter

    @property
    def capability(self) -> OfficialSwissModelLifecycleCapability:
        return OFFICIAL_COREAPI_LIFECYCLE_CAPABILITY

    async def create(self, preflight: SwissModelPreflight) -> SwissModelLifecycleStatus:
        """Admit exact MEV input, then pause before any undocumented create call."""
        admission = admit_swissmodel_submission(preflight)
        if not admission.admitted:
            return SwissModelLifecycleStatus.paused_for_admission(
                admission,
                message_code="swissmodel_submission_not_admitted",
            )
        return SwissModelLifecycleStatus.paused_for_admission(admission)

    async def poll(self, status: SwissModelLifecycleStatus) -> SwissModelLifecycleStatus:
        """Pause a recorded job because current docs lack bindable polling semantics."""
        if not status.is_recorded_non_terminal_job:
            return status.with_message("swissmodel_cancellation_not_available")
        return replace(
            status.with_message("swissmodel_polling_unavailable"),
            state="paused",
        )

    async def cancel(self, status: SwissModelLifecycleStatus) -> SwissModelLifecycleStatus:
        """Allow cancellation handling only for a recorded queued/running job.

        The current contract still pauses that job rather than issuing a guessed
        cancellation request.  Terminal or unrecorded records remain unchanged.
        """
        if not status.is_recorded_non_terminal_job:
            return status.with_message("swissmodel_cancellation_not_available")
        return replace(
            status.with_message("swissmodel_cancellation_unavailable"),
            state="paused",
        )

    async def model_result(self, status: SwissModelLifecycleStatus) -> SwissModelLifecycleStatus:
        """Pause instead of inferring a provider-approved result operation."""
        if not status.is_recorded_non_terminal_job:
            return status.with_message("swissmodel_cancellation_not_available")
        return replace(
            status.with_message("swissmodel_model_result_unavailable"),
            state="paused",
        )
