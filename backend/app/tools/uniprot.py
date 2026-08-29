"""UniProt REST client — Phase 1, step 1 ("Retrieve Proteome").

Streams the reference proteome for a taxon as FASTA and parses it into
records. Rate-limit aware: bounded retries with exponential backoff plus an
in-process cache keyed by taxon so re-runs within one server process never
hit the API twice.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import tempfile
import time
from dataclasses import dataclass
from hashlib import sha256
from typing import Any

import httpx

BASE_URL = "https://rest.uniprot.org"
PROTEOMES_URL = f"{BASE_URL}/proteomes/search"
USER_AGENT = "mev-pipeline/0.1 (multi-epitope vaccine pipeline; contact@example.org)"
MAX_RETRIES = 5
REQUEST_TIMEOUT = 300.0  # UniProt stream can be slow for large proteomes
CACHE_CONTRACT = "uniprot-reference-proteome-v4"
CACHE_FORMAT = "fasta"

# Cache files are addressed by the complete request contract, not only taxon.
# This intentionally makes all cache files from the older taxon-only contract
# unreachable: those files could contain a taxon-wide 2,106-record response
# for a reference-proteome request that must contain 2,105 records.
_DISK_CACHE_DIR = os.environ.get(
    "MEV_UNIPROT_CACHE", os.path.join(tempfile.gettempdir(), "mev-uniprot-cache")
)


def _disk_cache_path(
    key: int,
    reviewed_only: bool,
    proteome_id: str,
    *,
    query: str | None = None,
    release: str | None = None,
    format: str = CACHE_FORMAT,
) -> str:
    os.makedirs(_DISK_CACHE_DIR, exist_ok=True)
    request = {
        "taxonId": key,
        "reviewedOnly": reviewed_only,
        "query": query or f"proteome:{proteome_id}",
        "format": format,
        "release": release or "unknown",
    }
    digest = sha256(json.dumps(request, sort_keys=True).encode("utf-8")).hexdigest()[:24]
    return os.path.join(_DISK_CACHE_DIR, f"{CACHE_CONTRACT}_{digest}.{format}")


def _metadata_path(fasta_path: str) -> str:
    return f"{fasta_path}.json"

_HEADER_RE = re.compile(r">(sp|tr)\|([^|]+)\|([^ ]+)(?:\s+(.*))?$")

# Every result-affecting request value participates in the in-memory key.  The
# release component below contains a fingerprint of the complete proteome
# metadata response, so a changed UniProt release cannot reuse an older FASTA.
CacheKey = tuple[int, bool, str, str, str]
_cache: dict[CacheKey, tuple[list["ProteinRecord"], float]] = {}
_metadata_cache: dict[CacheKey, dict[str, Any]] = {}
_latest_metadata_key: dict[tuple[int, bool], CacheKey] = {}


@dataclass
class ProteinRecord:
    uniprot_id: str
    name: str
    sequence: str
    reviewed: bool = True


def parse_fasta(
    text: str,
    *,
    deduplicate: bool = True,
    strict_uniprot: bool = False,
) -> list[ProteinRecord]:
    """Parse FASTA records, optionally enforcing the UniProt response contract.

    Remote UniProt responses are strict: every record must have a valid
    ``sp|``/``tr|`` header and a non-empty sequence, and repeated accessions
    are collapsed deterministically (the reviewed record wins). User uploads
    intentionally use ``deduplicate=False`` so their record semantics are
    preserved exactly, including arbitrary headers and repeated accessions.
    """
    parsed: list[ProteinRecord] = []
    current: ProteinRecord | None = None

    def _finish(record: ProteinRecord | None) -> None:
        if record is None:
            return
        if strict_uniprot and (record.uniprot_id == "?" or not record.sequence):
            raise ValueError("UniProt returned a malformed FASTA record.")
        parsed.append(record)

    for line in text.splitlines():
        line = line.strip()
        if line.startswith(">"):
            _finish(current)
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
    _finish(current)

    if not deduplicate:
        return parsed

    by_accession: dict[str, ProteinRecord] = {}
    order: list[str] = []
    for record in parsed:
        # Uploads may use arbitrary headers. Only UniProt-like accessions are
        # deduplicated; unknown headers remain separate user records.
        if record.uniprot_id == "?":
            key = f"?:{len(order)}"
            order.append(key)
            by_accession[key] = record
            continue
        previous = by_accession.get(record.uniprot_id)
        if previous is None:
            order.append(record.uniprot_id)
            by_accession[record.uniprot_id] = record
        elif record.reviewed and not previous.reviewed:
            by_accession[record.uniprot_id] = record
    return [by_accession[accession] for accession in order]


def _store_metadata(cache_key: CacheKey, metadata: dict[str, Any]) -> None:
    """Store provenance under the complete request key and expose latest lookup."""
    _metadata_cache[cache_key] = metadata
    _latest_metadata_key[(cache_key[0], cache_key[1])] = cache_key


def _metadata_protein_count(proteome: dict[str, Any]) -> int | None:
    """Extract UniProt's authoritative protein count from either metadata shape."""
    direct = proteome.get("proteinCount")
    if direct is not None:
        return int(direct)
    for component in proteome.get("components") or []:
        if component.get("proteinCount") is not None:
            return int(component["proteinCount"])
    return None


async def _reference_proteome(client: httpx.AsyncClient, taxon_id: int) -> dict[str, Any] | None:
    """Resolve the one authoritative UniProt reference proteome for a taxon."""
    response = await client.get(
        PROTEOMES_URL,
        params={"query": f"organism_id:{taxon_id}", "format": "json", "size": "20"},
    )
    response.raise_for_status()
    results = response.json().get("results") or []
    matching = [
        r for r in results
        if str((r.get("taxonomy") or {}).get("taxonId")) == str(taxon_id)
    ]
    references = [r for r in matching if r.get("proteomeType") == "Reference proteome"]
    if len(references) == 1:
        return references[0]
    if len(references) > 1:
        raise RuntimeError(
            f"UniProt returned {len(references)} reference proteomes for taxon {taxon_id}; "
            "a unique reference proteome is required."
        )
    if not matching:
        return None
    raise RuntimeError(
        f"UniProt returned no reference proteome for taxon {taxon_id}; "
        "a non-reference proteome cannot be substituted."
    )


async def fetch_proteome(
    taxon_id: int | str,
    *,
    reviewed_only: bool = False,
    force_refresh: bool = False,
) -> list[ProteinRecord]:
    """Fetch the real UniProt reference proteome for a taxonomy ID.

    The default request is the complete reference-proteome stream. A reviewed-only
    request remains available as an explicit opt-in, but its filtered count is not
    treated as the complete UniProt metadata count.
    """
    key = int(taxon_id)
    async with httpx.AsyncClient(
        timeout=REQUEST_TIMEOUT,
        headers={"User-Agent": USER_AGENT},
        follow_redirects=True,
    ) as client:
        try:
            proteome = None
            metadata_error: Exception | None = None
            for attempt in range(MAX_RETRIES):
                try:
                    proteome = await _reference_proteome(client, key)
                    metadata_error = None
                    break
                except httpx.HTTPError as exc:
                    metadata_error = exc
                    if attempt < MAX_RETRIES - 1:
                        await asyncio.sleep(1.5 * (2 ** attempt))
            if metadata_error is not None:
                raise ConnectionError(f"UniProt proteome metadata unavailable: {metadata_error}") from metadata_error
        except httpx.HTTPError as exc:
            raise ConnectionError(f"UniProt proteome metadata unavailable: {exc}") from exc
        if proteome is None or not proteome.get("id"):
            raise RuntimeError(f"UniProt has no unambiguous reference proteome for taxon {key}.")
        proteome_id = str(proteome["id"])
        query = f"proteome:{proteome_id}"
        if reviewed_only:
            query += " AND reviewed:true"
        metadata_protein_count = _metadata_protein_count(proteome)
        if metadata_protein_count is None:
            raise RuntimeError(
                f"UniProt metadata for reference proteome {proteome_id} has no protein count; "
                "the complete dataset cannot be validated."
            )
        # Include the complete metadata payload in the release identity.  The
        # component count is the observed reference-proteome count (2105 for
        # taxon 208435); the taxon-wide search count is a different query and
        # must never share this cache entry.
        metadata_fingerprint = sha256(
            json.dumps(proteome, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        release = "|".join(
            str(value)
            for value in (
                proteome.get("modified"),
                metadata_protein_count,
                proteome_id,
                metadata_fingerprint,
            )
        )
        cache_key: CacheKey = (key, reviewed_only, query, CACHE_FORMAT, release)
        cached = _cache.get(cache_key)
        if cached and not force_refresh:
            cached_records = cached[0]
            if len(cached_records) != metadata_protein_count:
                # The metadata endpoint is authoritative for the current release;
                # never return an in-memory response from a different dataset.
                _cache.pop(cache_key, None)
            else:
                cached_metadata = {
                    "contract": CACHE_CONTRACT,
                    "source": f"{BASE_URL}/uniprotkb/stream",
                    "metadataSource": PROTEOMES_URL,
                    "query": query,
                    "format": CACHE_FORMAT,
                    "filters": {
                        "taxonId": key,
                        "proteomeId": proteome_id,
                        "reviewedOnly": reviewed_only,
                    },
                    "release": release,
                    "metadataFingerprint": metadata_fingerprint,
                    "proteome": {
                        "id": proteome_id,
                        "proteinCount": metadata_protein_count,
                        "modified": proteome.get("modified"),
                    },
                    "cached": True,
                    "cacheType": "cached-real",
                    "cacheState": "cached-real",
                    "recordCount": len(cached_records),
                    "referenceProteomeProteinCount": metadata_protein_count,
                    "returnedRecordCount": len(cached_records),
                    "countMatchesProteomeMetadata": len(cached_records) == metadata_protein_count,
                }
                _store_metadata(cache_key, cached_metadata)
                return cached_records

        disk_path = _disk_cache_path(
            key,
            reviewed_only,
            proteome_id,
            query=query,
            release=release,
        )
        meta_path = _metadata_path(disk_path)
        if not force_refresh and os.path.exists(disk_path) and os.path.exists(meta_path):
            try:
                with open(meta_path, "r", encoding="utf-8") as fh:
                    metadata = json.load(fh)
                if (
                    metadata.get("contract") == CACHE_CONTRACT
                    and metadata.get("query") == query
                    and metadata.get("format") == CACHE_FORMAT
                    and metadata.get("release") == release
                    and metadata.get("filters") == {
                        "taxonId": key,
                        "proteomeId": proteome_id,
                        "reviewedOnly": reviewed_only,
                    }
                ):
                    with open(disk_path, "r", encoding="utf-8", errors="replace") as fh:
                        records = parse_fasta(fh.read(), strict_uniprot=True)
                    if records and len(records) == metadata_protein_count:
                        cached_metadata = {
                            **metadata,
                            "cached": True,
                            "cacheType": "cached-real",
                            "cacheState": "cached-real",
                            "recordCount": len(records),
                            "referenceProteomeProteinCount": metadata_protein_count,
                            "returnedRecordCount": len(records),
                            "countMatchesProteomeMetadata": len(records) == metadata_protein_count,
                        }
                        _store_metadata(cache_key, cached_metadata)
                        return records
            except (OSError, ValueError, json.JSONDecodeError):
                # A stale, malformed, or mismatched cache is never a valid
                # response. Fetch the exact live request instead.
                pass

        params = {"query": query, "format": CACHE_FORMAT}
        last_error: Exception | None = None
        for attempt in range(MAX_RETRIES):
            try:
                response = await client.get(f"{BASE_URL}/uniprotkb/stream", params=params)
                if response.status_code in (429, 500, 502, 503, 504) and attempt < MAX_RETRIES - 1:
                    await asyncio.sleep(1.5 * (2 ** attempt))
                    continue
                response.raise_for_status()
                header_count = response.text.count(">")
                records = parse_fasta(response.text, strict_uniprot=True)
                if not records:
                    raise RuntimeError("UniProt returned an empty FASTA response.")
                if len(records) != metadata_protein_count:
                    raise RuntimeError(
                        f"UniProt reference proteome count mismatch: metadata={metadata_protein_count}, "
                        f"FASTA unique records={len(records)} for query {query!r}."
                    )
                metadata = {
                    "contract": CACHE_CONTRACT,
                    "source": f"{BASE_URL}/uniprotkb/stream",
                    "metadataSource": PROTEOMES_URL,
                    "query": query,
                    "format": CACHE_FORMAT,
                    "filters": {
                        "taxonId": key,
                        "proteomeId": proteome_id,
                        "reviewedOnly": reviewed_only,
                    },
                    "release": release,
                    "metadataFingerprint": metadata_fingerprint,
                    "proteome": {
                        "id": proteome_id,
                        "proteinCount": metadata_protein_count,
                        "modified": proteome.get("modified"),
                    },
                    "response": {
                        "statusCode": response.status_code,
                        "etag": response.headers.get("etag"),
                        "lastModified": response.headers.get("last-modified"),
                        "contentLength": response.headers.get("content-length"),
                        "contentType": response.headers.get("content-type"),
                        "totalResults": response.headers.get("x-total-results"),
                    },
                    "cached": False,
                    "cacheType": "real",
                    "cacheState": "fresh-real",
                    "recordCount": len(records),
                    "returnedRecordCount": len(records),
                    "fastaHeaderCount": header_count,
                    "duplicateAccessionsRemoved": header_count - len(records),
                    "countMatchesProteomeMetadata": len(records) == metadata_protein_count,
                    "referenceProteomeProteinCount": metadata_protein_count,
                }
                _cache[cache_key] = (records, time.time())
                _store_metadata(cache_key, metadata)
                try:
                    with open(disk_path, "w", encoding="utf-8") as fh:
                        fh.write(response.text)
                    with open(meta_path, "w", encoding="utf-8") as fh:
                        json.dump(metadata, fh, sort_keys=True)
                except OSError:
                    pass
                return records
            except httpx.HTTPStatusError as exc:
                last_error = exc
                if exc.response.status_code < 500 and attempt < MAX_RETRIES - 1:
                    await asyncio.sleep(1.5 * (2 ** attempt))
                    continue
            except RuntimeError:
                # A malformed or count-mismatched response is a source-integrity
                # failure, not a transient transport error; reject it directly.
                raise
            except (httpx.HTTPError, ValueError) as exc:
                last_error = exc
                if attempt < MAX_RETRIES - 1:
                    await asyncio.sleep(1.5 * (2 ** attempt))
        raise ConnectionError(f"UniProt unreachable after {MAX_RETRIES} attempts: {last_error}")


def fetch_metadata(
    taxon_id: int | str,
    *,
    reviewed_only: bool = False,
    query: str | None = None,
    format: str = CACHE_FORMAT,
    release: str | None = None,
) -> dict[str, Any] | None:
    """Return provenance only for the matching complete request contract.

    The runner uses the latest request for a taxon, while callers that have a
    query/release can require an exact cache identity.  No FASTA is returned
    across a query, format, or release boundary.
    """
    key = int(taxon_id)
    if query is None and release is None and format == CACHE_FORMAT:
        cache_key = _latest_metadata_key.get((key, reviewed_only))
        return _metadata_cache.get(cache_key) if cache_key else None
    for cache_key, metadata in reversed(list(_metadata_cache.items())):
        if cache_key[0] != key or cache_key[1] != reviewed_only or cache_key[3] != format:
            continue
        if query is not None and cache_key[2] != query:
            continue
        if release is not None and cache_key[4] != release:
            continue
        return metadata
    return None


def stats(records: list[ProteinRecord]) -> dict:
    lengths = [len(r.sequence) for r in records]
    return {
        "proteins": len(records),
        "totalResidues": sum(lengths),
        "minLength": min(lengths) if lengths else 0,
        "maxLength": max(lengths) if lengths else 0,
    }
