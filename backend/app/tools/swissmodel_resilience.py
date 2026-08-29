"""Bounded, restart-safe SWISS-MODEL CoreAPI lifecycle helpers.

This module separates three concerns which must never be conflated:

* ``OfficialSwissModelCoreApiHttpTransport`` is a server-only HTTP boundary for
  documented operations.  It creates an Authorization header transiently from
  runtime configuration and never returns, logs, or retains it.
* ``BoundedSwissModelOperationExecutor`` supplies finite connection/read/
  overall budgets and retries only idempotent read operations.  A create is
  attempted at most once because a timeout or connection failure leaves its
  outcome ambiguous.
* ``SwissModelResilientLifecycle`` persists only a public-safe, exact-input
  cache identity and lifecycle projection.  A fresh process can restore that
  record and poll its known request instead of submitting again.

No HTTP request is made while constructing these objects.  Tests provide local
fakes or ``httpx.MockTransport``; production wiring must provide routes and a
response decoder reviewed against the official CoreAPI documentation.
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from hashlib import sha256
import json
import math
import re
import time
from typing import Literal, Protocol
from urllib.parse import urljoin, urlparse

import httpx

from .swissmodel_contract import (
    OfficialAutomodelReceipt,
    SwissModelSubmissionAdmission,
    admit_swissmodel_submission,
)
from .swissmodel_lifecycle import SwissModelLifecycleStatus
from .swissmodel_runtime import (
    SWISSMODEL_PROVIDER,
    SwissModelPreflight,
    _runtime_token_for_official_transport,
    normalize_mev_sequence,
)


SwissModelRemoteState = Literal["queued", "running", "failed", "succeeded"]
SwissModelOperation = Literal[
    "automodel_create",
    "project_read",
    "project_models_summary_read",
]

_OFFICIAL_COREAPI_HOST = "swissmodel.expasy.org"
_OFFICIAL_PDB_MAX_BYTES = 25 * 1024 * 1024
_OFFICIAL_PDB_MAX_REDIRECTS = 3
_OFFICIAL_PDB_CONTENT_TYPES = frozenset({
    "application/pdb",
    "application/x-pdb",
    "chemical/pdb",
    "chemical/x-pdb",
})

_SAFE_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]*\Z")
_SAFE_FINGERPRINT = re.compile(r"[0-9a-f]{64}\Z")
_SAFE_INPUT_NAME = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")
_SAFE_INPUT_VALUE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_SENSITIVE_KEY_PARTS = frozenset({
    "authorization",
    "body",
    "coordinatetext",
    "credential",
    "cookie",
    "header",
    "modeltext",
    "payload",
    "rawrequest",
    "rawresponse",
    "requestbody",
    "secret",
    "sequence",
    "stacktrace",
    "token",
})


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


def _safe_timestamp(value: datetime | str | None) -> str | None:
    """Normalize only real ISO timestamps; reject arbitrary provider prose."""
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


def _safe_https_url(value: object) -> str | None:
    """Return only a credential-free HTTPS URL on the official CoreAPI host."""
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


def _normalise_provider(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("SWISS-MODEL provider must be a string")
    provider = value.strip().lower().replace("-", "").replace("_", "")
    if provider != SWISSMODEL_PROVIDER:
        raise ValueError("only the SWISS-MODEL provider is accepted")
    return provider


def _normalise_method(value: object) -> str:
    safe = _safe_identifier(value)
    if safe is None:
        raise ValueError("SWISS-MODEL method must be a safe opaque identifier")
    return safe.lower()


def _normalise_model_inputs(
    values: Mapping[str, object] | None,
) -> tuple[tuple[str, str], ...]:
    """Canonicalize only safe, non-body model-input discriminators.

    Inputs contribute to an idempotency key, not a provider request body.  The
    exact MEV sequence is represented only by its fingerprint; names associated
    with auth, request/response material, or a sequence are refused outright.
    """
    if values is None:
        return ()
    if not isinstance(values, Mapping):
        raise ValueError("model inputs must be a mapping of safe scalar values")

    normalized: dict[str, str] = {}
    for raw_name, raw_value in values.items():
        if not isinstance(raw_name, str):
            raise ValueError("model input names must be strings")
        name = raw_name.strip().lower().replace("-", "_")
        if (
            not _SAFE_INPUT_NAME.fullmatch(name)
            or any(part in name for part in _SENSITIVE_KEY_PARTS)
        ):
            raise ValueError("model input name is not safe for lifecycle persistence")
        if isinstance(raw_value, bool):
            value = "true" if raw_value else "false"
        elif isinstance(raw_value, int) and not isinstance(raw_value, bool):
            value = str(raw_value)
        elif isinstance(raw_value, str):
            value = raw_value.strip()
        else:
            raise ValueError("model input values must be safe scalar values")
        if not _SAFE_INPUT_VALUE.fullmatch(value):
            raise ValueError("model input value is not safe for lifecycle persistence")
        if name in normalized:
            raise ValueError("model input names must be unique after normalization")
        normalized[name] = value
    return tuple(sorted(normalized.items()))


def _has_unsafe_persisted_content(value: object) -> bool:
    """Reject a supplied record that already contains prohibited material."""
    if isinstance(value, Mapping):
        for key, child in value.items():
            if not isinstance(key, str):
                return True
            normalized_key = "".join(character for character in key.lower() if character.isalnum())
            if any(part in normalized_key for part in _SENSITIVE_KEY_PARTS):
                return True
            if _has_unsafe_persisted_content(child):
                return True
    elif isinstance(value, (list, tuple, set)):
        return any(_has_unsafe_persisted_content(child) for child in value)
    elif isinstance(value, str):
        parsed = urlparse(value)
        if parsed.scheme in {"http", "https"} and (
            parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            return True
    return False


@dataclass(frozen=True)
class SwissModelRetryPolicy:
    """Finite deadlines and retry limits for one documented operation."""

    connect_timeout_seconds: float = 5.0
    read_timeout_seconds: float = 20.0
    overall_timeout_seconds: float = 30.0
    max_attempts: int = 3
    initial_backoff_seconds: float = 0.5
    max_backoff_seconds: float = 5.0

    def __post_init__(self) -> None:
        for name in (
            "connect_timeout_seconds",
            "read_timeout_seconds",
            "overall_timeout_seconds",
            "initial_backoff_seconds",
            "max_backoff_seconds",
        ):
            value = getattr(self, name)
            if not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be a finite positive number")
        if not isinstance(self.max_attempts, int) or not 1 <= self.max_attempts <= 5:
            raise ValueError("max_attempts must be an integer from 1 through 5")
        if self.initial_backoff_seconds > self.max_backoff_seconds:
            raise ValueError("initial backoff must not exceed the maximum backoff")

    def httpx_timeout(self) -> httpx.Timeout:
        """Return the finite connect/read timeout used by the HTTP boundary."""
        return httpx.Timeout(
            connect=self.connect_timeout_seconds,
            read=self.read_timeout_seconds,
            write=self.read_timeout_seconds,
            pool=self.connect_timeout_seconds,
        )


class SwissModelOperationError(Exception):
    """Sanitized, typed operation failure with no provider body or headers."""

    def __init__(
        self,
        kind: Literal[
            "configuration",
            "http_error",
            "protocol",
            "rate_limited",
            "timeout",
            "transport",
        ],
        *,
        retryable: bool,
        retry_after_seconds: float | None = None,
    ) -> None:
        self.kind = kind
        self.retryable = retryable
        self.retry_after_seconds = retry_after_seconds
        super().__init__(kind)


class SwissModelRetryExhausted(Exception):
    """A finite retry budget ended without a verified provider result."""

    def __init__(self, kind: str) -> None:
        self.kind = kind
        super().__init__(kind)


def _parse_retry_after(value: object, *, now: datetime | None = None) -> float | None:
    """Parse standard Retry-After guidance without retaining the header value."""
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    try:
        seconds = float(candidate)
    except ValueError:
        try:
            retry_at = parsedate_to_datetime(candidate)
        except (TypeError, ValueError, IndexError, OverflowError):
            return None
        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=timezone.utc)
        reference = now or datetime.now(timezone.utc)
        seconds = (retry_at.astimezone(timezone.utc) - reference.astimezone(timezone.utc)).total_seconds()
    if not math.isfinite(seconds):
        return None
    return max(0.0, seconds)


def _http_operation_error(response: httpx.Response) -> SwissModelOperationError:
    """Reduce an HTTP response to retry-safe classification only."""
    status_code = response.status_code
    retry_after = _parse_retry_after(response.headers.get("Retry-After"))
    if status_code == 429:
        return SwissModelOperationError(
            "rate_limited",
            retryable=True,
            retry_after_seconds=retry_after,
        )
    return SwissModelOperationError(
        "http_error",
        retryable=status_code in {408, 425} or 500 <= status_code <= 599,
        retry_after_seconds=retry_after,
    )


def _classify_operation_exception(exc: BaseException) -> SwissModelOperationError:
    if isinstance(exc, SwissModelOperationError):
        return exc
    if isinstance(exc, httpx.HTTPStatusError):
        return _http_operation_error(exc.response)
    if isinstance(exc, (asyncio.TimeoutError, httpx.TimeoutException)):
        return SwissModelOperationError("timeout", retryable=True)
    if isinstance(exc, httpx.HTTPError):
        return SwissModelOperationError("transport", retryable=True)
    raise exc


class BoundedSwissModelOperationExecutor:
    """Run safe reads with bounded retries and an operation-wide deadline."""

    _RETRYABLE_OPERATIONS = frozenset({
        "project_read",
        "project_models_summary_read",
    })

    def __init__(
        self,
        policy: SwissModelRetryPolicy = SwissModelRetryPolicy(),
        *,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.policy = policy
        self._sleep = sleep
        self._monotonic = monotonic

    async def run_once(self, invoke: Callable[[], Awaitable[object]]) -> object:
        """Use the finite overall deadline once, with no automatic retry."""
        return await self._attempt(invoke, self.policy.overall_timeout_seconds)

    async def run_idempotent(
        self,
        operation: SwissModelOperation,
        invoke: Callable[[], Awaitable[object]],
    ) -> object:
        """Retry only documented read operations, never an automodel create."""
        if operation not in self._RETRYABLE_OPERATIONS:
            raise ValueError(f"{operation} is not safe for automatic retry")

        deadline = self._monotonic() + self.policy.overall_timeout_seconds
        last_error: SwissModelOperationError | None = None
        for attempt in range(self.policy.max_attempts):
            remaining = deadline - self._monotonic()
            if remaining <= 0:
                raise SwissModelRetryExhausted("timeout")
            try:
                return await self._attempt(invoke, remaining)
            except SwissModelOperationError as exc:
                last_error = exc
                if not exc.retryable or attempt + 1 >= self.policy.max_attempts:
                    raise SwissModelRetryExhausted(exc.kind) from None

                exponential_delay = min(
                    self.policy.max_backoff_seconds,
                    self.policy.initial_backoff_seconds * (2 ** attempt),
                )
                delay = exponential_delay
                if exc.retry_after_seconds is not None:
                    delay = min(
                        self.policy.max_backoff_seconds,
                        max(exponential_delay, exc.retry_after_seconds),
                    )
                remaining_after_failure = deadline - self._monotonic()
                if remaining_after_failure <= 0:
                    raise SwissModelRetryExhausted("timeout") from None
                await self._sleep(min(delay, remaining_after_failure))

        # The loop always returns or raises.  This is a defensive fallback for
        # static type checking and must not include any provider detail.
        raise SwissModelRetryExhausted(last_error.kind if last_error else "transport")

    async def _attempt(
        self,
        invoke: Callable[[], Awaitable[object]],
        timeout_seconds: float,
    ) -> object:
        try:
            return await asyncio.wait_for(invoke(), timeout=timeout_seconds)
        except Exception as exc:
            try:
                raise _classify_operation_exception(exc)
            except SwissModelOperationError:
                raise


@dataclass(frozen=True)
class OfficialSwissModelProject:
    """Only an already-verified, public-safe project state from a decoder."""

    state: SwissModelRemoteState
    updated_at: datetime | str | None = None

    def __post_init__(self) -> None:
        if self.state not in {"queued", "running", "failed", "succeeded"}:
            raise ValueError("project state is not a supported lifecycle state")


@dataclass(frozen=True)
class OfficialSwissModelModelSummary:
    """Only safe model provenance selected by an official response decoder."""

    model_id: str | None = None
    model_url: str | None = None
    updated_at: datetime | str | None = None


class OfficialSwissModelLifecycleTransport(Protocol):
    """Documented operations used by the resilient lifecycle coordinator."""

    async def create_automodel(
        self,
        *,
        target_sequences: str,
    ) -> OfficialAutomodelReceipt:
        """Create a model using the documented direct ``automodel`` input."""

    async def read_project(self, *, project_id: str) -> OfficialSwissModelProject:
        """Read the known official project/job state."""

    async def read_project_model_summary(
        self,
        *,
        project_id: str,
    ) -> OfficialSwissModelModelSummary:
        """Read safe model summary metadata for the known official project."""


@dataclass(frozen=True)
class OfficialSwissModelCoreApiRoutes:
    """Reviewed, canonical CoreAPI routes supplied by production wiring.

    Route builders are deliberately injected rather than inferred from opaque
    provider identifiers.  Every generated route must remain on the official
    HTTPS host, use the canonical trailing slash, and bind a singular
    ``/project/{project_id}/`` path.  The PDB route receives both safe IDs so
    it cannot silently download a model from a different provider project.
    """

    automodel_url: str
    project_url: Callable[[str], str]
    project_models_summary_url: Callable[[str], str]
    model_pdb_url: Callable[[str, str], str]

    def __post_init__(self) -> None:
        _require_official_url(self.automodel_url, canonical_trailing_slash=True)

    def project(self, project_id: str) -> str:
        return _require_project_bound_url(self.project_url(project_id), project_id)

    def project_models_summary(self, project_id: str) -> str:
        return _require_project_bound_url(
            self.project_models_summary_url(project_id),
            project_id,
        )

    def model_pdb(self, project_id: str, model_id: str) -> str:
        return _require_project_bound_url(
            self.model_pdb_url(project_id, model_id),
            project_id,
            model_id=model_id,
        )


def _require_official_url(
    value: object,
    *,
    canonical_trailing_slash: bool = False,
) -> str:
    candidate = _safe_https_url(value)
    if candidate is None:
        raise ValueError(
            "official CoreAPI URL must be credential-free HTTPS on swissmodel.expasy.org"
        )
    if canonical_trailing_slash and not urlparse(candidate).path.endswith("/"):
        raise ValueError("official CoreAPI routes must use a canonical trailing slash")
    return candidate


def _require_project_bound_url(
    value: object,
    project_id: str,
    *,
    model_id: str | None = None,
) -> str:
    """Accept only a canonical singular official route bound to known IDs."""
    candidate = _require_official_url(value, canonical_trailing_slash=True)
    segments = [segment for segment in urlparse(candidate).path.split("/") if segment]
    if len(segments) < 2 or segments[0] != "project" or segments[1] != project_id:
        raise ValueError("official CoreAPI route must be bound to the recorded project identifier")
    if model_id is not None and model_id not in segments[2:]:
        raise ValueError("official CoreAPI PDB route must be bound to the recorded model identifier")
    return candidate


class OfficialSwissModelResponseDecoder(Protocol):
    """A reviewed decoder that reduces response bodies to typed safe values."""

    def automodel_receipt(self, payload: object) -> OfficialAutomodelReceipt:
        """Decode only safe direct-create provenance."""

    def project(self, payload: object) -> OfficialSwissModelProject:
        """Decode only a verified lifecycle state and timestamp."""

    def project_model_summary(self, payload: object) -> OfficialSwissModelModelSummary:
        """Decode only safe model identifiers/URL/timestamp."""


class SwissModelPdbDownloadError(Exception):
    """A fixed-code PDB rejection that never carries provider text or URLs."""

    _CODES = frozenset({
        "pdb_content_type_invalid",
        "pdb_exact_validation_failed",
        "pdb_redirect_invalid",
        "pdb_redirect_limit",
        "pdb_response_size_invalid",
        "pdb_response_too_large",
        "pdb_text_invalid",
    })

    def __init__(self, code: str) -> None:
        self.code = code if code in self._CODES else "pdb_text_invalid"
        super().__init__(self.code)


@dataclass(frozen=True)
class OfficialSwissModelPdbQualification:
    """Transient exact-coordinate result with a deliberately safe projection."""

    project_id: str
    model_id: str
    model_url: str
    sequence_identity_validation: Mapping[str, object]
    _coordinate_text: str = field(repr=False)
    _expected_sequence: str = field(repr=False)

    def transient_structure_input(self) -> dict[str, object]:
        """Return coordinates only for the existing transient Step-11-2 boundary."""
        return {
            "sequence": self._expected_sequence,
            "source": "real",
            "provider": "SWISS-MODEL",
            "method": "official CoreAPI PDB",
            "modelUrl": self.model_url,
            "modelFormat": "pdb",
            "coordinateText": self._coordinate_text,
        }

    def public_provenance(self) -> dict[str, object]:
        """Return metadata safe for lifecycle/API storage; never coordinates."""
        return {
            "source": "real",
            "provider": SWISSMODEL_PROVIDER,
            "method": "automodel",
            "projectId": self.project_id,
            "modelId": self.model_id,
            "modelUrl": self.model_url,
            "sequenceIdentityValidation": dict(self.sequence_identity_validation),
            "coordinateDataAvailable": True,
            "syntheticValues": False,
        }


class OfficialSwissModelPdbQualificationTransport(Protocol):
    """The guarded exact-PDB operation required before an official success."""

    async def qualify_model_pdb(
        self,
        *,
        project_id: str,
        model_id: str,
        expected_sequence: str,
    ) -> OfficialSwissModelPdbQualification:
        """Return only exact, fully covered transient coordinates."""


class OfficialSwissModelCoreApiHttpTransport:
    """Server-only CoreAPI HTTP transport with no persistent credential state.

    This boundary knows *which* documented operation is being called, but a
    response decoder and routes must be supplied explicitly so it never guesses
    provider schema fields or endpoint paths.  It performs no retries itself;
    the coordinator owns idempotency decisions.
    """

    def __init__(
        self,
        *,
        routes: OfficialSwissModelCoreApiRoutes,
        decoder: OfficialSwissModelResponseDecoder,
        policy: SwissModelRetryPolicy = SwissModelRetryPolicy(),
        http_transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._routes = routes
        self._decoder = decoder
        self._policy = policy
        self._http_transport = http_transport

    async def create_automodel(
        self,
        *,
        target_sequences: str,
    ) -> OfficialAutomodelReceipt:
        """Issue one documented direct create; callers must not auto-repeat it."""
        normalized = normalize_mev_sequence(target_sequences)
        if normalized is None or normalized != target_sequences:
            raise ValueError("automodel create requires the exact normalized MEV sequence")
        payload = await self._request_json(
            "POST",
            self._routes.automodel_url,
            json_data={"target_sequences": normalized},
        )
        try:
            return self._decoder.automodel_receipt(payload)
        except (TypeError, ValueError, KeyError) as exc:
            raise SwissModelOperationError("protocol", retryable=False) from exc

    async def read_project(self, *, project_id: str) -> OfficialSwissModelProject:
        """Read one known project via the reviewed official route."""
        safe_project_id = _safe_identifier(project_id)
        if safe_project_id is None:
            raise ValueError("project read requires a safe recorded project identifier")
        payload = await self._request_json(
            "GET",
            self._routes.project(safe_project_id),
        )
        try:
            return self._decoder.project(payload)
        except (TypeError, ValueError, KeyError) as exc:
            raise SwissModelOperationError("protocol", retryable=False) from exc

    async def read_project_model_summary(
        self,
        *,
        project_id: str,
    ) -> OfficialSwissModelModelSummary:
        """Read only safe summary metadata for one known project."""
        safe_project_id = _safe_identifier(project_id)
        if safe_project_id is None:
            raise ValueError("model summary requires a safe recorded project identifier")
        payload = await self._request_json(
            "GET",
            self._routes.project_models_summary(safe_project_id),
        )
        try:
            return self._decoder.project_model_summary(payload)
        except (TypeError, ValueError, KeyError) as exc:
            raise SwissModelOperationError("protocol", retryable=False) from exc

    async def read_model_pdb(self, *, project_id: str, model_id: str) -> bytes:
        """Download only a bounded, official-host PDB response.

        This transport operation validates the provider-approved route, every
        redirect target, content type, and streamed response size.  It returns
        transient bytes only; callers that need a terminal result must use
        :meth:`qualify_model_pdb` to invoke the exact assembled-MEV boundary.
        """
        content, _final_url = await self._download_model_pdb(
            project_id=project_id,
            model_id=model_id,
        )
        return content

    async def qualify_model_pdb(
        self,
        *,
        project_id: str,
        model_id: str,
        expected_sequence: str,
    ) -> OfficialSwissModelPdbQualification:
        """Guard an official PDB then reuse Step 11-2 exact validation.

        No coordinate text is persisted or serialized here.  The only return
        value carrying coordinates is a transient handoff object whose public
        projection contains safe provenance and the validated 100% identity /
        coverage record only.
        """
        safe_project_id = _safe_identifier(project_id)
        safe_model_id = _safe_identifier(model_id)
        expected = normalize_mev_sequence(expected_sequence)
        if safe_project_id is None or safe_model_id is None or expected is None:
            raise ValueError("PDB qualification requires safe IDs and an exact normalized MEV")

        content, final_url = await self._download_model_pdb(
            project_id=safe_project_id,
            model_id=safe_model_id,
        )
        try:
            coordinate_text = content.decode("utf-8")
        except UnicodeDecodeError:
            raise SwissModelPdbDownloadError("pdb_text_invalid") from None
        if not coordinate_text.strip():
            raise SwissModelPdbDownloadError("pdb_text_invalid")

        # Import lazily to avoid a module-import cycle.  This is the preserved
        # manual attachment boundary: it parses PDB C-alpha records and accepts
        # only an exact assembled sequence, which enforces complete required
        # coordinate coverage rather than trusting provider metadata.
        from .runner_additions import validate_mev_structure_input

        try:
            validated = validate_mev_structure_input(
                {
                    "sequence": expected,
                    "source": "real",
                    "provider": "SWISS-MODEL",
                    "method": "official CoreAPI PDB",
                    "modelUrl": final_url,
                    "modelFormat": "pdb",
                    "coordinateText": coordinate_text,
                },
                expected,
            )
            validation = validated["sequenceIdentityValidation"]
            if (
                not isinstance(validation, Mapping)
                or validation.get("identityPercent") != 100.0
                or validation.get("coveragePercent") != 100.0
            ):
                raise ValueError("exact coordinate qualification is incomplete")
        except (TypeError, ValueError):
            raise SwissModelPdbDownloadError("pdb_exact_validation_failed") from None

        return OfficialSwissModelPdbQualification(
            project_id=safe_project_id,
            model_id=safe_model_id,
            model_url=final_url,
            sequence_identity_validation=dict(validation),
            _coordinate_text=coordinate_text,
            _expected_sequence=expected,
        )

    async def _download_model_pdb(
        self,
        *,
        project_id: str,
        model_id: str,
    ) -> tuple[bytes, str]:
        safe_project_id = _safe_identifier(project_id)
        safe_model_id = _safe_identifier(model_id)
        if safe_project_id is None or safe_model_id is None:
            raise ValueError("PDB read requires safe recorded project and model identifiers")
        route = self._routes.model_pdb(safe_project_id, safe_model_id)
        return await self._request_pdb_bytes(route)

    async def _request_json(
        self,
        method: str,
        url: str,
        *,
        json_data: Mapping[str, str] | None = None,
    ) -> object:
        response = await self._request(method, url, json_data=json_data, accept="application/json")
        try:
            return response.json()
        except ValueError as exc:
            raise SwissModelOperationError("protocol", retryable=False) from exc

    async def _request_pdb_bytes(self, url: str) -> tuple[bytes, str]:
        """Fetch a PDB with explicit redirect, MIME, and streaming-size guards."""
        official_url = _require_official_url(url, canonical_trailing_slash=True)
        token = _runtime_token_for_official_transport()
        if token is None:
            raise SwissModelOperationError("configuration", retryable=False)
        headers = {
            "Accept": ", ".join(sorted(_OFFICIAL_PDB_CONTENT_TYPES)),
            "Authorization": f"Token {token}",
        }

        async def download() -> tuple[bytes, str]:
            async with httpx.AsyncClient(
                timeout=self._policy.httpx_timeout(),
                follow_redirects=False,
                headers=headers,
                transport=self._http_transport,
            ) as client:
                current_url = official_url
                for _redirect_count in range(_OFFICIAL_PDB_MAX_REDIRECTS + 1):
                    async with client.stream("GET", current_url) as response:
                        if 300 <= response.status_code < 400:
                            location = response.headers.get("Location")
                            if not location or "\r" in location or "\n" in location:
                                raise SwissModelPdbDownloadError("pdb_redirect_invalid")
                            redirected = _safe_https_url(urljoin(current_url, location))
                            if redirected is None:
                                raise SwissModelPdbDownloadError("pdb_redirect_invalid")
                            current_url = redirected
                            continue
                        if response.status_code >= 400:
                            raise _http_operation_error(response)

                        content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
                        if content_type not in _OFFICIAL_PDB_CONTENT_TYPES:
                            raise SwissModelPdbDownloadError("pdb_content_type_invalid")

                        declared_size = response.headers.get("Content-Length")
                        if declared_size is not None:
                            try:
                                size = int(declared_size)
                            except ValueError:
                                raise SwissModelPdbDownloadError("pdb_response_size_invalid") from None
                            if size < 0:
                                raise SwissModelPdbDownloadError("pdb_response_size_invalid")
                            if size > _OFFICIAL_PDB_MAX_BYTES:
                                raise SwissModelPdbDownloadError("pdb_response_too_large")

                        content = bytearray()
                        async for chunk in response.aiter_bytes():
                            if len(content) + len(chunk) > _OFFICIAL_PDB_MAX_BYTES:
                                raise SwissModelPdbDownloadError("pdb_response_too_large")
                            content.extend(chunk)
                        return bytes(content), current_url
                raise SwissModelPdbDownloadError("pdb_redirect_limit")

        try:
            return await asyncio.wait_for(
                download(),
                timeout=self._policy.overall_timeout_seconds,
            )
        except SwissModelPdbDownloadError:
            raise
        except Exception as exc:
            try:
                raise _classify_operation_exception(exc)
            except SwissModelOperationError:
                raise

    async def _request(
        self,
        method: str,
        url: str,
        *,
        json_data: Mapping[str, str] | None = None,
        accept: str,
    ) -> httpx.Response:
        official_url = _require_official_url(url, canonical_trailing_slash=True)
        token = _runtime_token_for_official_transport()
        if token is None:
            raise SwissModelOperationError("configuration", retryable=False)

        # The header is intentionally a local request-construction value.  It
        # is absent from this object's fields, exceptions, status records, and
        # cache serialization.
        headers = {
            "Accept": accept,
            "Authorization": f"Token {token}",
        }
        try:
            async with httpx.AsyncClient(
                timeout=self._policy.httpx_timeout(),
                follow_redirects=False,
                headers=headers,
                transport=self._http_transport,
            ) as client:
                response = await asyncio.wait_for(
                    client.request(method, official_url, json=json_data),
                    timeout=self._policy.overall_timeout_seconds,
                )
        except Exception as exc:
            try:
                raise _classify_operation_exception(exc)
            except SwissModelOperationError:
                raise

        if response.status_code >= 400:
            raise _http_operation_error(response)
        return response


@dataclass(frozen=True)
class SwissModelLifecycleKey:
    """Canonical safe identity for a single exact official lifecycle input."""

    mev_fingerprint: str
    provider: str
    method: str
    model_inputs: tuple[tuple[str, str], ...] = ()
    selection_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "mev_fingerprint", _safe_fingerprint(self.mev_fingerprint) or "")
        if not self.mev_fingerprint:
            raise ValueError("MEV fingerprint must be an exact SHA-256 value")
        object.__setattr__(self, "provider", _normalise_provider(self.provider))
        object.__setattr__(self, "method", _normalise_method(self.method))
        safe_selection = _safe_identifier(self.selection_id) if self.selection_id is not None else None
        if self.selection_id is not None and safe_selection is None:
            raise ValueError("selection identifier must be a safe opaque identifier")
        object.__setattr__(self, "selection_id", safe_selection)
        object.__setattr__(self, "model_inputs", _normalise_model_inputs(dict(self.model_inputs)))

    @classmethod
    def from_admission(
        cls,
        admission: SwissModelSubmissionAdmission,
        *,
        model_inputs: Mapping[str, object] | None = None,
    ) -> "SwissModelLifecycleKey":
        if not admission.admitted:
            raise ValueError("only an admitted official submission has a lifecycle key")
        return cls(
            mev_fingerprint=admission.mev_fingerprint or "",
            provider=SWISSMODEL_PROVIDER,
            method=admission.method or "",
            model_inputs=_normalise_model_inputs(model_inputs),
            selection_id=admission.selection_id,
        )

    @property
    def cache_key(self) -> str:
        canonical = json.dumps(
            {
                "mevFingerprint": self.mev_fingerprint,
                "method": self.method,
                "modelInputs": list(self.model_inputs),
                "provider": self.provider,
                "selectionId": self.selection_id,
            },
            separators=(",", ":"),
            sort_keys=True,
        )
        return sha256(canonical.encode("utf-8")).hexdigest()

    def public_identity(self) -> dict[str, object]:
        """Return only the safe exact-match components needed after restart."""
        result: dict[str, object] = {
            "provider": self.provider,
            "method": self.method,
            "mevFingerprint": self.mev_fingerprint,
            "modelInputs": dict(self.model_inputs),
        }
        if self.selection_id is not None:
            result["selectionId"] = self.selection_id
        return result


@dataclass(frozen=True)
class SwissModelLifecycleRecord:
    """Safe persisted state that can resume only its exact known job."""

    key: SwissModelLifecycleKey
    status: SwissModelLifecycleStatus
    created_at: str
    updated_at: str

    def to_persisted(self) -> dict[str, object]:
        """Serialize the strict safe record; omit sequence, body, token, and errors."""
        created_at = _safe_timestamp(self.created_at) or _now_timestamp()
        updated_at = _safe_timestamp(self.updated_at) or created_at
        return {
            "cacheKey": self.key.cache_key,
            "cacheIdentity": self.key.public_identity(),
            "lifecycle": self.status.public_result(),
            "createdAt": created_at,
            "updatedAt": updated_at,
        }

    @classmethod
    def from_persisted(cls, payload: object) -> "SwissModelLifecycleRecord | None":
        """Validate a record before it can suppress or resume a submission."""
        if not isinstance(payload, Mapping) or _has_unsafe_persisted_content(payload):
            return None
        cache_key = payload.get("cacheKey")
        identity = payload.get("cacheIdentity")
        lifecycle = payload.get("lifecycle")
        if not isinstance(cache_key, str) or not isinstance(identity, Mapping) or not isinstance(lifecycle, Mapping):
            return None
        try:
            model_inputs = identity.get("modelInputs")
            key = SwissModelLifecycleKey(
                mev_fingerprint=str(identity.get("mevFingerprint") or ""),
                provider=str(identity.get("provider") or ""),
                method=str(identity.get("method") or ""),
                model_inputs=_normalise_model_inputs(model_inputs if isinstance(model_inputs, Mapping) else None),
                selection_id=identity.get("selectionId") if identity.get("selectionId") is not None else None,
            )
        except (TypeError, ValueError):
            return None
        if cache_key != key.cache_key:
            return None

        state = lifecycle.get("status")
        if state not in {"queued", "running", "paused", "failed", "succeeded"}:
            return None
        if lifecycle.get("provider") != key.provider:
            return None
        if lifecycle.get("mevFingerprint") != key.mev_fingerprint:
            return None
        if lifecycle.get("method") != key.method:
            return None
        if key.selection_id is not None and lifecycle.get("selectionId") != key.selection_id:
            return None
        if key.selection_id is None and lifecycle.get("selectionId") is not None:
            return None

        status = SwissModelLifecycleStatus(
            state=state,
            message_code=lifecycle.get("messageCode") if isinstance(lifecycle.get("messageCode"), str) else None,
            mev_fingerprint=key.mev_fingerprint,
            method=key.method,
            selection_id=key.selection_id,
            request_id=lifecycle.get("requestId") if isinstance(lifecycle.get("requestId"), str) else None,
            model_id=lifecycle.get("modelId") if isinstance(lifecycle.get("modelId"), str) else None,
            model_url=lifecycle.get("modelUrl") if isinstance(lifecycle.get("modelUrl"), str) else None,
            pdb_qualified=lifecycle.get("pdbQualified") is True,
            submitted_at=lifecycle.get("submittedAt") if isinstance(lifecycle.get("submittedAt"), str) else None,
            updated_at=lifecycle.get("updatedAt") if isinstance(lifecycle.get("updatedAt"), str) else None,
            completed_at=lifecycle.get("completedAt") if isinstance(lifecycle.get("completedAt"), str) else None,
        )
        # The status projection normalizes identifiers, URLs, and timestamps.
        # Refuse tampered values rather than accepting a lossy cache hit.
        if status.public_result() != dict(lifecycle):
            return None
        created_at = _safe_timestamp(payload.get("createdAt"))
        updated_at = _safe_timestamp(payload.get("updatedAt"))
        if created_at is None or updated_at is None:
            return None
        return cls(key=key, status=status, created_at=created_at, updated_at=updated_at)


class SwissModelLifecycleStore:
    """Small in-memory index whose public records are safe to persist elsewhere."""

    def __init__(self) -> None:
        self._records: dict[str, SwissModelLifecycleRecord] = {}

    def get(self, key: SwissModelLifecycleKey) -> SwissModelLifecycleRecord | None:
        record = self._records.get(key.cache_key)
        return record if record is not None and record.key == key else None

    def put(self, record: SwissModelLifecycleRecord) -> None:
        self._records[record.key.cache_key] = record

    def restore(self, payload: object) -> SwissModelLifecycleRecord | None:
        record = SwissModelLifecycleRecord.from_persisted(payload)
        if record is not None:
            self.put(record)
        return record


@dataclass(frozen=True)
class SwissModelLifecycleOutcome:
    """A safe status, its exact cache record, and optional transient PDB handoff."""

    status: SwissModelLifecycleStatus
    record: SwissModelLifecycleRecord | None = None
    qualified_pdb: OfficialSwissModelPdbQualification | None = field(default=None, repr=False)

    def public_result(self) -> dict[str, object]:
        return self.status.public_result()

    def persisted_record(self) -> dict[str, object] | None:
        return self.record.to_persisted() if self.record is not None else None


def _status_from_admission(
    admission: SwissModelSubmissionAdmission,
    *,
    state: Literal["queued", "paused"],
    message_code: str | None = None,
    request_id: str | None = None,
    model_id: str | None = None,
    model_url: str | None = None,
    submitted_at: datetime | str | None = None,
) -> SwissModelLifecycleStatus:
    timestamp = _safe_timestamp(submitted_at) or _now_timestamp()
    return SwissModelLifecycleStatus(
        state=state,
        message_code=message_code,
        mev_fingerprint=admission.mev_fingerprint,
        method=admission.method,
        selection_id=admission.selection_id,
        request_id=request_id,
        model_id=model_id,
        model_url=model_url,
        submitted_at=timestamp if state == "queued" else None,
        updated_at=timestamp,
    )


def _retry_message_code(kind: str) -> str:
    if kind == "rate_limited":
        return "swissmodel_lifecycle_rate_limited"
    if kind == "timeout":
        return "swissmodel_lifecycle_timeout"
    return "swissmodel_lifecycle_retry_exhausted"


class SwissModelResilientLifecycle:
    """Create once, cache by exact safe inputs, and resume only known jobs."""

    def __init__(
        self,
        transport: OfficialSwissModelLifecycleTransport,
        *,
        policy: SwissModelRetryPolicy = SwissModelRetryPolicy(),
        store: SwissModelLifecycleStore | None = None,
        pdb_transport: OfficialSwissModelPdbQualificationTransport | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._transport = transport
        self._pdb_transport = pdb_transport
        self._store = store or SwissModelLifecycleStore()
        self._executor = BoundedSwissModelOperationExecutor(
            policy,
            sleep=sleep,
            monotonic=monotonic,
        )

    @property
    def store(self) -> SwissModelLifecycleStore:
        return self._store

    async def create_or_resume(
        self,
        preflight: SwissModelPreflight,
        *,
        model_inputs: Mapping[str, object] | None = None,
        persisted_record: object | None = None,
    ) -> SwissModelLifecycleOutcome:
        """Reuse exact state first; otherwise make at most one create attempt."""
        admission = admit_swissmodel_submission(preflight)
        if not admission.admitted:
            return SwissModelLifecycleOutcome(
                SwissModelLifecycleStatus.paused_for_admission(
                    admission,
                    message_code="swissmodel_submission_not_admitted",
                )
            )

        try:
            key = SwissModelLifecycleKey.from_admission(admission, model_inputs=model_inputs)
        except ValueError:
            invalid = _status_from_admission(
                admission,
                state="paused",
                message_code="swissmodel_lifecycle_record_invalid",
            )
            return self._save(key=None, status=invalid)

        if persisted_record is not None:
            restored = self._store.restore(persisted_record)
            if restored is None or restored.key != key:
                invalid = _status_from_admission(
                    admission,
                    state="paused",
                    message_code="swissmodel_lifecycle_record_invalid",
                )
                return self._save(key=key, status=invalid)

        existing = self._store.get(key)
        if existing is not None:
            normalized_sequence = normalize_mev_sequence(preflight.normalized_sequence)
            if normalized_sequence is None:
                invalid = _status_from_admission(
                    admission,
                    state="paused",
                    message_code="swissmodel_lifecycle_record_invalid",
                )
                return self._save(key=key, status=invalid, existing=existing)
            if existing.status.is_recorded_non_terminal_job:
                return await self._resume(existing, expected_sequence=normalized_sequence)
            if existing.status.state == "succeeded" and not existing.status.pdb_qualified:
                return await self._qualify_terminal_success(
                    existing,
                    existing.status,
                    expected_sequence=normalized_sequence,
                )
            return SwissModelLifecycleOutcome(existing.status, existing)

        return await self._create(admission, key)

    async def _create(
        self,
        admission: SwissModelSubmissionAdmission,
        key: SwissModelLifecycleKey,
    ) -> SwissModelLifecycleOutcome:
        normalized_sequence = normalize_mev_sequence(admission.normalized_sequence)
        if normalized_sequence is None:
            return self._save(
                key=key,
                status=_status_from_admission(
                    admission,
                    state="paused",
                    message_code="swissmodel_submission_not_admitted",
                ),
            )
        try:
            receipt = await self._executor.run_once(
                lambda: self._transport.create_automodel(target_sequences=normalized_sequence)
            )
        except SwissModelOperationError:
            # The provider may have accepted the create before the failure.  Do
            # not repeat it automatically on this process or a future restart.
            return self._save(
                key=key,
                status=_status_from_admission(
                    admission,
                    state="paused",
                    message_code="swissmodel_create_outcome_unknown",
                ),
            )

        if not isinstance(receipt, OfficialAutomodelReceipt):
            return self._save(
                key=key,
                status=_status_from_admission(
                    admission,
                    state="paused",
                    message_code="swissmodel_create_outcome_unknown",
                ),
            )
        queued = _status_from_admission(
            admission,
            state="queued",
            request_id=receipt.request_id,
            model_id=receipt.model_id,
            model_url=receipt.model_url,
            submitted_at=receipt.submitted_at,
        )
        if not queued.is_recorded_non_terminal_job:
            return self._save(
                key=key,
                status=replace(
                    queued,
                    state="paused",
                    message_code="swissmodel_create_request_id_missing",
                    submitted_at=None,
                ),
            )
        return self._save(key=key, status=queued)

    async def _resume(
        self,
        record: SwissModelLifecycleRecord,
        *,
        expected_sequence: str,
    ) -> SwissModelLifecycleOutcome:
        status = record.status
        request_id = _safe_identifier(status.request_id)
        if request_id is None or not status.is_recorded_non_terminal_job:
            return self._save(
                key=record.key,
                status=replace(
                    status,
                    state="paused",
                    message_code="swissmodel_lifecycle_record_invalid",
                    updated_at=_now_timestamp(),
                ),
                existing=record,
            )
        try:
            project = await self._executor.run_idempotent(
                "project_read",
                lambda: self._transport.read_project(project_id=request_id),
            )
            if not isinstance(project, OfficialSwissModelProject):
                raise SwissModelOperationError("protocol", retryable=False)

            summary: OfficialSwissModelModelSummary | None = None
            if project.state != "failed":
                summary = await self._executor.run_idempotent(
                    "project_models_summary_read",
                    lambda: self._transport.read_project_model_summary(project_id=request_id),
                )
                if not isinstance(summary, OfficialSwissModelModelSummary):
                    raise SwissModelOperationError("protocol", retryable=False)
        except SwissModelRetryExhausted as exc:
            return self._save(
                key=record.key,
                status=replace(
                    status,
                    state="paused",
                    message_code=_retry_message_code(exc.kind),
                    updated_at=_now_timestamp(),
                ),
                existing=record,
            )
        except SwissModelOperationError as exc:
            return self._save(
                key=record.key,
                status=replace(
                    status,
                    state="paused",
                    message_code=_retry_message_code(exc.kind),
                    updated_at=_now_timestamp(),
                ),
                existing=record,
            )

        if status.state == "running" and project.state == "queued":
            return self._save(
                key=record.key,
                status=replace(
                    status,
                    state="paused",
                    message_code="swissmodel_lifecycle_transition_invalid",
                    updated_at=_now_timestamp(),
                ),
                existing=record,
            )

        updated_at = _safe_timestamp(project.updated_at) or _now_timestamp()
        next_status = replace(
            status,
            state=project.state,
            message_code=(
                "swissmodel_provider_terminal_failure"
                if project.state == "failed"
                else None
            ),
            pdb_qualified=False,
            updated_at=updated_at,
            completed_at=updated_at if project.state in {"failed", "succeeded"} else None,
        )
        if summary is not None:
            next_status = replace(
                next_status,
                model_id=summary.model_id or next_status.model_id,
                model_url=summary.model_url or next_status.model_url,
                updated_at=_safe_timestamp(summary.updated_at) or next_status.updated_at,
            )
        if project.state == "succeeded":
            return await self._qualify_terminal_success(
                record,
                next_status,
                expected_sequence=expected_sequence,
            )
        return self._save(key=record.key, status=next_status, existing=record)

    async def _qualify_terminal_success(
        self,
        record: SwissModelLifecycleRecord,
        status: SwissModelLifecycleStatus,
        *,
        expected_sequence: str,
    ) -> SwissModelLifecycleOutcome:
        """Prevent a provider terminal state from becoming local success unqualified."""
        project_id = _safe_identifier(status.request_id)
        model_id = _safe_identifier(status.model_id)
        if self._pdb_transport is None or project_id is None or model_id is None:
            return self._save(
                key=record.key,
                status=replace(
                    status,
                    state="paused",
                    message_code="swissmodel_pdb_qualification_required",
                    pdb_qualified=False,
                    completed_at=None,
                    updated_at=_now_timestamp(),
                ),
                existing=record,
            )

        try:
            qualified = await self._pdb_transport.qualify_model_pdb(
                project_id=project_id,
                model_id=model_id,
                expected_sequence=expected_sequence,
            )
        except (SwissModelPdbDownloadError, SwissModelOperationError, ValueError):
            return self._save(
                key=record.key,
                status=replace(
                    status,
                    state="paused",
                    message_code="swissmodel_pdb_qualification_failed",
                    pdb_qualified=False,
                    completed_at=None,
                    updated_at=_now_timestamp(),
                ),
                existing=record,
            )

        if not isinstance(qualified, OfficialSwissModelPdbQualification):
            return self._save(
                key=record.key,
                status=replace(
                    status,
                    state="paused",
                    message_code="swissmodel_pdb_qualification_failed",
                    pdb_qualified=False,
                    completed_at=None,
                    updated_at=_now_timestamp(),
                ),
                existing=record,
            )

        qualified_status = replace(
            status,
            state="succeeded",
            message_code=None,
            model_id=qualified.model_id,
            model_url=qualified.model_url,
            pdb_qualified=True,
            updated_at=_now_timestamp(),
            completed_at=status.completed_at or _now_timestamp(),
        )
        outcome = self._save(
            key=record.key,
            status=qualified_status,
            existing=record,
        )
        return replace(outcome, qualified_pdb=qualified)

    def _save(
        self,
        *,
        key: SwissModelLifecycleKey | None,
        status: SwissModelLifecycleStatus,
        existing: SwissModelLifecycleRecord | None = None,
    ) -> SwissModelLifecycleOutcome:
        if key is None:
            return SwissModelLifecycleOutcome(status)
        timestamp = _now_timestamp()
        record = SwissModelLifecycleRecord(
            key=key,
            status=status,
            created_at=existing.created_at if existing is not None else timestamp,
            updated_at=timestamp,
        )
        self._store.put(record)
        return SwissModelLifecycleOutcome(status, record)
