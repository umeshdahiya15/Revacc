"""Local, contract-neutral fixtures for Issue 7 baseline tests.

This module deliberately contains no provider URL, HTTP method, request schema,
authorization value, or provider response.  Until the official contract is
reviewed in a later task, the fake exposes only the design-level lifecycle
operation vocabulary and records whether a caller attempted to use it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
from typing import Literal
from urllib.parse import parse_qsl, urlparse


OfficialLifecycleOperation = Literal[
    "discovery",
    "submission",
    "polling",
    "cancellation",
    "download",
]

# These names are design-level lifecycle operations, not endpoint paths,
# HTTP methods, request fields, or a claim about a provider API schema.
OFFICIAL_LIFECYCLE_OPERATIONS = frozenset({
    "discovery",
    "submission",
    "polling",
    "cancellation",
    "download",
})


@dataclass(frozen=True)
class CompletedStep92Mev:
    """A completed, normalized step-9-2 MEV plus its exact fingerprint."""

    raw_sequence: str
    normalized_sequence: str
    fingerprint: str

    @classmethod
    def from_raw_sequence(cls, raw_sequence: str) -> "CompletedStep92Mev":
        normalized = "".join(str(raw_sequence or "").split()).upper()
        if not normalized:
            raise ValueError("fixture MEV sequence must not be empty")
        return cls(
            raw_sequence=raw_sequence,
            normalized_sequence=normalized,
            fingerprint=sha256(normalized.encode("utf-8")).hexdigest(),
        )

    def step_result(self) -> dict[str, str]:
        """The completed step-9-2 result shape consumed by the API route."""
        return {"sequence": self.normalized_sequence}

    def runner_session(self) -> dict[str, dict[str, str]]:
        """The transient sequence shape consumed by the step-11-2 runner."""
        return {"mev_construct": {"sequence": self.normalized_sequence}}


def completed_step_9_2_mev() -> CompletedStep92Mev:
    """Return a whitespace-normalization fixture for the exact MEV AVLG."""
    return CompletedStep92Mev.from_raw_sequence(" aV\nL g ")


@dataclass
class FakeOfficialContractTransport:
    """A no-I/O recorder for the future official-contract adapter boundary.

    It cannot store a credential, URL, header, request body, or provider
    response.  Baseline tests assert it remains unused while the production
    code has no authenticated official client.
    """

    calls: list[OfficialLifecycleOperation] = field(default_factory=list)
    cache_sink: list[dict[str, object]] = field(default_factory=list)

    async def invoke(self, operation: OfficialLifecycleOperation) -> None:
        if operation not in OFFICIAL_LIFECYCLE_OPERATIONS:
            raise AssertionError(f"unsupported official lifecycle operation: {operation}")
        self.calls.append(operation)

    def assert_unused(self) -> None:
        assert self.calls == []
        assert self.cache_sink == []


_RESIDUE_NAMES = {
    "A": "ALA", "C": "CYS", "D": "ASP", "E": "GLU", "F": "PHE",
    "G": "GLY", "H": "HIS", "I": "ILE", "K": "LYS", "L": "LEU",
    "M": "MET", "N": "ASN", "P": "PRO", "Q": "GLN", "R": "ARG",
    "S": "SER", "T": "THR", "V": "VAL", "W": "TRP", "Y": "TYR",
}


def exact_mev_pdb(sequence: str) -> str:
    """Build local full-backbone coordinates for an exact fixture sequence."""
    normalized = "".join(str(sequence or "").split()).upper()
    lines: list[str] = []
    serial = 1
    for residue_number, amino_acid in enumerate(normalized, start=1):
        residue_name = _RESIDUE_NAMES[amino_acid]
        x = (residue_number - 1) * 3.8
        for atom, dx, dy, element in (
            ("N", -1.3, 1.0, "N"),
            ("CA", 0.0, 0.0, "C"),
            ("C", 1.3, 1.0, "C"),
        ):
            lines.append(
                f"ATOM  {serial:5d} {atom:>4} {residue_name:>3} A{residue_number:4d}    "
                f"{x + dx:8.3f}{dy:8.3f}{0.0:8.3f}  1.00 90.00           {element:>2}"
            )
            serial += 1
    return "\n".join([*lines, "END"]) + "\n"


_UNSAFE_PUBLIC_KEY_PARTS = (
    "token",
    "secret",
    "authorization",
    "header",
    "rawproviderpayload",
    "rawrequest",
    "rawresponse",
    "stacktrace",
    "coordinatetext",
    "modeltext",
)


def _mapping_keys(value: object):
    if isinstance(value, dict):
        for key, child in value.items():
            yield str(key)
            yield from _mapping_keys(child)
    elif isinstance(value, (list, tuple, set)):
        for child in value:
            yield from _mapping_keys(child)


def _strings(value: object):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for child in value.values():
            yield from _strings(child)
    elif isinstance(value, (list, tuple, set)):
        for child in value:
            yield from _strings(child)


def assert_public_sinks_safe(
    *sinks: object,
    transient_coordinate_text: str | None = None,
) -> None:
    """Assert public/log/cache fixtures contain no sensitive fields or PDB body."""
    for key in _mapping_keys(sinks):
        normalized_key = "".join(character for character in key.lower() if character.isalnum())
        assert not any(part in normalized_key for part in _UNSAFE_PUBLIC_KEY_PARTS), key

    for text in _strings(sinks):
        if transient_coordinate_text:
            assert transient_coordinate_text not in text
        parsed = urlparse(text)
        if parsed.scheme in {"http", "https"}:
            assert parsed.username is None and parsed.password is None
            assert not any(
                "token" in key.lower() or "secret" in key.lower()
                for key, _value in parse_qsl(parsed.query, keep_blank_values=True)
            )
