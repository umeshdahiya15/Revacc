"""ESMFold sequence-to-PDB provider.

The public ESM Atlas endpoint returns real ESMFold PDB coordinates for short
sequences. It is intentionally kept separate from the local model runtime so
Step 11-2 can use the API when local OpenFold is unavailable.
"""
from __future__ import annotations

import os
import re

import httpx


ESMFOLD_API_URL = os.environ.get(
    "ESMFOLD_API_URL", "https://api.esmatlas.com/foldSequence/v1/pdb/"
)
ESMFOLD_MAX_SEQUENCE_LENGTH = 400
ESMFOLD_TIMEOUT = float(os.environ.get("ESMFOLD_TIMEOUT", "600"))
_AMINO_ACID_SEQUENCE = re.compile(r"[ACDEFGHIKLMNPQRSTVWY]+\Z")


class ESMFoldError(RuntimeError):
    """A safe, actionable ESMFold provider error."""


async def predict_pdb(sequence: str) -> str:
    """Fold one exact amino-acid sequence through the public ESMFold API."""
    normalized = "".join(str(sequence or "").split()).upper()
    if not normalized or not _AMINO_ACID_SEQUENCE.fullmatch(normalized):
        raise ESMFoldError("ESMFold requires a non-empty standard amino-acid sequence")
    if len(normalized) > ESMFOLD_MAX_SEQUENCE_LENGTH:
        raise ESMFoldError(
            f"ESMFold public API supports at most {ESMFOLD_MAX_SEQUENCE_LENGTH} aa; "
            f"the exact MEV is {len(normalized)} aa"
        )

    try:
        async with httpx.AsyncClient(
            timeout=ESMFOLD_TIMEOUT,
            follow_redirects=True,
            headers={"Content-Type": "text/plain", "Accept": "text/plain"},
        ) as client:
            response = await client.post(ESMFOLD_API_URL, content=normalized)
            response.raise_for_status()
    except httpx.HTTPError as exc:
        raise ESMFoldError(f"ESMFold API request failed: {exc.__class__.__name__}") from exc

    pdb_text = response.text
    if not pdb_text.strip() or "ATOM" not in pdb_text:
        raise ESMFoldError("ESMFold API returned no usable PDB coordinates")
    return pdb_text
