"""AlphaFold DB REST client — Phase 11-2 (AlphaFold structure prediction).

Thin async wrapper around the AlphaFold Protein Structure Database REST API:

  https://alphafold.ebi.ac.uk/api/prediction/UNIPROT_ACCESSION

Returns a list with a single JSON record containing:
  - pdbUrl / cifUrl / bcifUrl  — structure files (PDB, mmCIF, Binary mmCIF)
  - pLDDT confidence scores per-residue
  - paeImageUrl / predictedAlignedError
  - sequence, uniprotId, gene, organismScientificName
  - version, toolUsed, modelType

The endpoint was confirmed live: an anonymous GET returns full structure
metadata for any reviewed UniProt entry that has an AlphaFold model.
"""
from __future__ import annotations

import httpx
from dataclasses import dataclass
from typing import Optional

try:
    from .api_cache import cached_async_api_call
except ImportError:
    cached_async_api_call = None

API_BASE = "https://alphafold.ebi.ac.uk/api/prediction"
REQUEST_TIMEOUT = 60.0
MAX_RETRIES = 3
_BACKOFF = (2.0, 5.0, 10.0)


@dataclass
class AFDBEntry:
    """Parsed AlphaFold DB prediction entry."""

    uniprot_id: str
    gene: Optional[str]
    organism: Optional[str]
    sequence: str
    pdb_url: Optional[str]
    cif_url: Optional[str]
    bcif_url: Optional[str]
    plddt: Optional[float]
    version: int
    tool_used: Optional[str]
    entry_id: str
    raw: dict


@dataclass
class AFDBResult:
    """Result of a single AFDB prediction lookup."""

    found: bool
    entry: Optional[AFDBEntry]
    message: str


async def fetch_prediction(uniprot_id: str) -> AFDBResult:
    """Fetch a single AlphaFold prediction by UniProt accession.

    Returns an :class:`AFDBResult` whose ``found`` is ``True`` when a model
    exists, ``False`` when AlphaFold has no model for the sequence.
    """
    url = f"{API_BASE}/{uniprot_id}"
    last_error: Exception | None = None

    for attempt in range(MAX_RETRIES):
        try:
            async with httpx.AsyncClient(
                timeout=REQUEST_TIMEOUT,
                headers={"User-Agent": "mev-pipeline/0.1"},
            ) as client:
                resp = await client.get(url, follow_redirects=True)
                resp.raise_for_status()
                data = resp.json()

                # The API returns a list (with one element for a single ID).
                if isinstance(data, list) and data:
                    record = data[0]
                elif isinstance(data, dict) and "record" in data:
                    record = data["record"]
                else:
                    return AFDBResult(
                        found=False,
                        entry=None,
                        message=f"No AlphaFold model for {uniprot_id}",
                    )

                plddt = _mean_plddt(record)

                return AFDBResult(
                    found=True,
                    entry=AFDBEntry(
                        uniprot_id=record.get("uniprotId", uniprot_id),
                        gene=record.get("gene"),
                        organism=record.get("organismScientificName"),
                        sequence=record.get("sequence", ""),
                        pdb_url=record.get("pdbUrl"),
                        cif_url=record.get("cifUrl"),
                        bcif_url=record.get("bcifUrl"),
                        plddt=plddt,
                        version=int(record.get("latestVersion") or 0),
                        tool_used=record.get("toolUsed"),
                        entry_id=record.get("entryId", ""),
                        raw=record,
                    ),
                    message=f"AlphaFold model found (pLDDT={plddt:.1f})" if plddt else "AlphaFold model found",
                )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404 and attempt == 0:
                return AFDBResult(
                    found=False,
                    entry=None,
                    message=f"No AlphaFold model for {uniprot_id}",
                )
            last_error = exc
            if attempt < MAX_RETRIES - 1:
                import asyncio

                await asyncio.sleep(_BACKOFF[attempt])
        except (httpx.HTTPError, ValueError) as exc:
            last_error = exc
            if attempt < MAX_RETRIES - 1:
                import asyncio

                await asyncio.sleep(_BACKOFF[attempt])

    return AFDBResult(
        found=False,
        entry=None,
        message=f"AlphaFold fetch failed after {MAX_RETRIES} attempts: {last_error}",
    )


def _mean_plddt(record: dict) -> Optional[float]:
    """Extract mean pLDDT from the per-residue confidence scores.

    The confidence field is a list of per-residue pLDDT values (0-100).
    """
    plDDT = record.get("plddt") if isinstance(record.get("plddt"), list) else None
    if not plDDT:
        # Some endpoints return a single globalMetricValue
        return record.get("globalMetricValue")
    valid = [v for v in plDDT if isinstance(v, (int, float))]
    if not valid:
        return None
    return sum(valid) / len(valid)


async def fetch_structure_pdb(pdb_url: str) -> Optional[str]:
    """Download a PDB file from a URL and return its text content."""
    if not pdb_url:
        return None
    try:
        async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
            resp = await client.get(pdb_url)
            resp.raise_for_status()
            return resp.text
    except httpx.HTTPError:
        return None
