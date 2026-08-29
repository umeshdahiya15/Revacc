"""ESMFold sequence-to-PDB provider.

When a CUDA GPU is available and the ESMFold weights are cached locally,
the provider runs inference on-device for speed and reliability.  The public
ESM Atlas API remains a fallback for CPU-only environments.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import sys

import httpx


logger = logging.getLogger(__name__)

ESMFOLD_API_URL = os.environ.get(
    "ESMFOLD_API_URL", "https://api.esmatlas.com/foldSequence/v1/pdb/"
)
ESMFOLD_MAX_SEQUENCE_LENGTH = 400
ESMFOLD_TIMEOUT = float(os.environ.get("ESMFOLD_TIMEOUT", "600"))
_AMINO_ACID_SEQUENCE = re.compile(r"[ACDEFGHIKLMNPQRSTVWY]+\Z")


class ESMFoldError(RuntimeError):
    """A safe, actionable ESMFold provider error."""


# ---------------------------------------------------------------------------
# Local ESMFold (torch.hub on CUDA)
# ---------------------------------------------------------------------------

_local_model = None
_local_available: bool | None = None  # None = not checked yet


def _local_esmfold_available() -> bool:
    """Return True if local ESMFold can run (CUDA + weights present)."""
    global _local_available
    if _local_available is not None:
        return _local_available

    try:
        import torch
        if not torch.cuda.is_available():
            _local_available = False
            return False

        weights_path = os.path.expanduser(
            "~/.cache/torch/hub/checkpoints/esmfold_3B_v1.pt"
        )
        if not os.path.exists(weights_path):
            _local_available = False
            return False

        _local_available = True
        return True
    except Exception:
        _local_available = False
        return False


def _load_local_model():
    """Load ESMFold model via torch.hub on CUDA."""
    global _local_model
    if _local_model is not None:
        return _local_model

    import torch
    logger.info("Loading local ESMFold model on %s ...", torch.cuda.get_device_name(0))
    _local_model = torch.hub.load("facebookresearch/esm:main", "esmfold_3B_v1")
    _local_model = _local_model.eval().cuda()
    logger.info("Local ESMFold model loaded successfully.")
    return _local_model


def _predict_local(sequence: str) -> str:
    """Run ESMFold inference on local CUDA device."""
    model = _load_local_model()
    import torch
    with torch.no_grad():
        pdb_text = model.infer_pdb(sequence)
    return pdb_text


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def predict_pdb(sequence: str) -> str:
    """Fold one exact amino-acid sequence through ESMFold.

    Tries local CUDA inference first, falls back to the public API.
    """
    normalized = "".join(str(sequence or "").split()).upper()
    if not normalized or not _AMINO_ACID_SEQUENCE.fullmatch(normalized):
        raise ESMFoldError("ESMFold requires a non-empty standard amino-acid sequence")

    # --- Local inference path (preferred when available) ---
    if _local_esmfold_available() and len(normalized) <= ESMFOLD_MAX_SEQUENCE_LENGTH:
        try:
            coordinate_text = await asyncio.to_thread(_predict_local, normalized)
            if coordinate_text and "ATOM" in coordinate_text:
                logger.info("ESMFold: local CUDA inference succeeded (%d aa)", len(normalized))
                return coordinate_text
        except Exception as exc:
            logger.warning("ESMFold local inference failed, falling back to API: %s", exc)

    # --- Public API fallback ---
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
