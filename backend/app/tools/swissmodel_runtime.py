"""Server-only SWISS-MODEL runtime capability and MEV preflight boundary.

This module is the sole backend location that inspects
``SWISSMODEL_API_TOKEN``.  It deliberately reduces that runtime-only value to
an opaque capability result: no caller can receive, serialize, log, cache, or
persist the credential (or a hash, length, or other token-derived value).

No provider transport is implemented here.  The preflight only establishes
whether a future official-contract adapter may consider admission; it never
contacts SWISS-MODEL.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import os
import re
from typing import Literal


SWISSMODEL_PROVIDER = "swissmodel"

_AMINO_ACID_SEQUENCE = re.compile(r"[ACDEFGHIKLMNPQRSTVWY]+\Z")
_SHA256_FINGERPRINT = re.compile(r"[0-9a-f]{64}\Z")


@dataclass(frozen=True)
class SwissModelRuntimeCapability:
    """An opaque, serializable capability outcome without credential material."""

    available: bool
    message_code: str | None = None
    reason: str | None = None
    action: str | None = None


@dataclass(frozen=True)
class SwissModelPreflight:
    """Safe preflight state for a future official-contract adapter.

    ``normalized_sequence`` is retained only for the in-process adapter
    boundary.  ``public_result`` intentionally excludes it, because public
    lifecycle state needs only a status, code/action, provider, and the safe
    sequence fingerprint when preflight is ready.
    """

    ready: bool
    status: Literal["ready", "paused"]
    message_code: str | None = None
    reason: str | None = None
    action: str | None = None
    normalized_sequence: str | None = None
    mev_fingerprint: str | None = None

    def public_result(self) -> dict[str, str]:
        """Return the safe status shape suitable for public serialization."""
        result = {
            "status": self.status,
            "provider": SWISSMODEL_PROVIDER,
        }
        if self.message_code:
            result["messageCode"] = self.message_code
        if self.action:
            result["action"] = self.action
        if self.mev_fingerprint:
            result["mevFingerprint"] = self.mev_fingerprint
        return result


def _runtime_token_for_official_transport() -> str | None:
    """Return a runtime token only to the private official HTTP boundary.

    This is intentionally the only escape hatch from this module's opaque
    capability model.  The transport consumes the value immediately while
    constructing a request header; it must not retain, serialize, cache, log,
    or return it.  All application-facing callers continue to receive only
    ``SwissModelRuntimeCapability`` or preflight results.
    """
    runtime_value = os.environ.get("SWISSMODEL_API_TOKEN")
    if not isinstance(runtime_value, str):
        return None
    token = runtime_value.strip()
    return token or None


def _runtime_secret_is_configured() -> bool:
    """Inspect only whether the deployment secret is available."""
    return _runtime_token_for_official_transport() is not None


def swissmodel_runtime_capability() -> SwissModelRuntimeCapability:
    """Return only whether the server runtime can attempt official preflight."""
    if _runtime_secret_is_configured():
        return SwissModelRuntimeCapability(available=True)
    return SwissModelRuntimeCapability(
        available=False,
        message_code="swissmodel_runtime_secret_missing",
        reason="no SWISSMODEL_API_TOKEN is configured in the server runtime.",
        action=(
            "Configure the SWISS-MODEL server runtime secret, then retry Step 11-2."
        ),
    )


def normalize_mev_sequence(value: object) -> str | None:
    """Return a canonical amino-acid MEV sequence, or ``None`` when invalid."""
    if not isinstance(value, str):
        return None
    normalized = "".join(value.split()).upper()
    if not normalized or not _AMINO_ACID_SEQUENCE.fullmatch(normalized):
        return None
    return normalized


def mev_sequence_fingerprint(normalized_sequence: str) -> str:
    """Compute the safe SHA-256 MEV fingerprint for a normalized sequence."""
    return sha256(normalized_sequence.encode("utf-8")).hexdigest()


def _normalise_fingerprint(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower()
    return normalized if _SHA256_FINGERPRINT.fullmatch(normalized) else None


def _paused_preflight(
    *,
    message_code: str,
    reason: str,
    action: str,
) -> SwissModelPreflight:
    return SwissModelPreflight(
        ready=False,
        status="paused",
        message_code=message_code,
        reason=reason,
        action=action,
    )


def preflight_swissmodel_submission(
    *,
    step_9_2_completed: bool,
    step_9_2_sequence: object,
    current_mev_sequence: object,
    current_mev_fingerprint: object,
) -> SwissModelPreflight:
    """Validate configuration and exact-MEV binding before any provider call.

    This is deliberately narrower than official-contract admission: Task 20.3
    will add verified provider-contract eligibility.  Until then, a caller
    receives a ready capability only after the local prerequisites are exact;
    it must still pause rather than submit through an undocumented client.
    """
    capability = swissmodel_runtime_capability()
    if not capability.available:
        return _paused_preflight(
            message_code=capability.message_code or "swissmodel_runtime_unavailable",
            reason=capability.reason or "SWISS-MODEL runtime configuration is unavailable.",
            action=capability.action or "Configure the SWISS-MODEL server runtime and retry.",
        )

    if not step_9_2_completed:
        return _paused_preflight(
            message_code="swissmodel_step_9_2_incomplete",
            reason="Step 9-2 must complete before official structure-model preflight.",
            action="Complete MEV assembly, then retry Step 11-2.",
        )

    assembled_sequence = normalize_mev_sequence(step_9_2_sequence)
    if assembled_sequence is None:
        return _paused_preflight(
            message_code="swissmodel_step_9_2_sequence_invalid",
            reason="Step 9-2 has no valid normalized MEV sequence for official modeling.",
            action="Re-run MEV assembly and retry Step 11-2.",
        )

    current_sequence = normalize_mev_sequence(current_mev_sequence)
    if current_sequence is None:
        return _paused_preflight(
            message_code="swissmodel_current_mev_missing",
            reason="The current MEV sequence is unavailable for official structure-model preflight.",
            action="Restore the completed MEV assembly in this run, then retry Step 11-2.",
        )

    expected_fingerprint = mev_sequence_fingerprint(assembled_sequence)
    supplied_fingerprint = _normalise_fingerprint(current_mev_fingerprint)
    current_fingerprint = mev_sequence_fingerprint(current_sequence)
    if (
        current_sequence != assembled_sequence
        or supplied_fingerprint != current_fingerprint
        or current_fingerprint != expected_fingerprint
    ):
        return _paused_preflight(
            message_code="swissmodel_mev_fingerprint_mismatch",
            reason="The current MEV does not match the completed Step 9-2 fingerprint.",
            action="Re-run MEV assembly and retry Step 11-2 with the exact current construct.",
        )

    return SwissModelPreflight(
        ready=True,
        status="ready",
        normalized_sequence=assembled_sequence,
        mev_fingerprint=expected_fingerprint,
    )
