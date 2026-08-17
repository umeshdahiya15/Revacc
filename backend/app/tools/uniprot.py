"""UniProt REST client — Phase 1, step 1 ("Retrieve Proteome").

Streams the reference proteome for a taxon as FASTA and parses it into
records. Rate-limit aware: bounded retries with exponential backoff plus an
in-process cache keyed by taxon so re-runs within one server process never
hit the API twice.
"""
from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass

import httpx

BASE_URL = "https://rest.uniprot.org"
USER_AGENT = "mev-pipeline/0.1 (multi-epitope vaccine pipeline; contact@example.org)"
MAX_RETRIES = 4
REQUEST_TIMEOUT = 60.0

_HEADER_RE = re.compile(r">(sp|tr)\|([^|]+)\|([^ ]+) (.*?)(?: OX=.*)?$")

_cache: dict[int, tuple[list["ProteinRecord"], float]] = {}


@dataclass
class ProteinRecord:
    uniprot_id: str
    name: str
    sequence: str
    reviewed: bool = True


def parse_fasta(text: str) -> list[ProteinRecord]:
    records: list[ProteinRecord] = []
    current: ProteinRecord | None = None
    for line in text.splitlines():
        line = line.strip()
        if line.startswith(">"):
            if current is not None:
                records.append(current)
            match = _HEADER_RE.match(line)
            if match:
                current = ProteinRecord(
                    uniprot_id=match.group(2),
                    name=match.group(3),
                    sequence="",
                    reviewed=match.group(1) == "sp",
                )
            else:
                current = ProteinRecord(uniprot_id="?", name=line[1:], sequence="")
        elif current is not None:
            current.sequence += line
    if current is not None:
        records.append(current)
    return records


async def fetch_proteome(
    taxon_id: int | str,
    *,
    reviewed_only: bool = True,
    force_refresh: bool = False,
) -> list[ProteinRecord]:
    """Fetch (and cache) the proteome FASTA for a taxonomy ID."""
    key = int(taxon_id)
    cached = _cache.get(key)
    if cached and not force_refresh:
        records, _ = cached
        return records

    query = f"taxonomy_id:{key}"
    if reviewed_only:
        query += " AND reviewed:true"

    url = f"{BASE_URL}/uniprotkb/stream"
    params = {"query": query, "format": "fasta"}

    async with httpx.AsyncClient(
        timeout=REQUEST_TIMEOUT,
        headers={"User-Agent": USER_AGENT},
        follow_redirects=True,
    ) as client:
        last_error: Exception | None = None
        for attempt in range(MAX_RETRIES):
            try:
                response = await client.get(url, params=params)
                if response.status_code in (429, 500, 502, 503, 504) and attempt < MAX_RETRIES - 1:
                    await asyncio.sleep(1.5 * (2 ** attempt))
                    continue
                response.raise_for_status()
                records = parse_fasta(response.text)
                _cache[key] = (records, __import__("time").time())
                return records
            except httpx.HTTPStatusError as exc:
                last_error = exc
                if exc.response.status_code < 500 and attempt < MAX_RETRIES - 1:
                    await asyncio.sleep(1.5 * (2 ** attempt))
                    continue
            except httpx.HTTPError as exc:
                last_error = exc
                await asyncio.sleep(1.5 * (2 ** attempt))

        raise ConnectionError(f"UniProt unreachable after {MAX_RETRIES} attempts: {last_error}")


def stats(records: list[ProteinRecord]) -> dict:
    lengths = [len(r.sequence) for r in records]
    return {
        "proteins": len(records),
        "totalResidues": sum(lengths),
        "minLength": min(lengths) if lengths else 0,
        "maxLength": max(lengths) if lengths else 0,
    }
