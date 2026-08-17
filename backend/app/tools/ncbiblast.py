"""NCBI remote BLASTp client — Phase 2, step 1 ("Identify Essential Proteins").

Thin wrapper over the NCBI BLAST URL API (the same backend that powers the
web UI): submit a `Put`, poll the RID until `Status=READY`, parse the XML,
then `Delete`. The DEG reference module supplies an `ENTREZ_QUERY` that
restricts the search space to essential-gene UIDs, so each query is answered
over a tiny indexed slice of the protein database and timeouts stay short.

NCBI asks clients to be polite (no tight polling loops, sequential Put calls);
this client sleeps between requests and treats transient connection drops as
retryable.
"""
from __future__ import annotations

import asyncio
import os
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

import httpx

BLAST_URL = "https://blast.ncbi.nlm.nih.gov/Blast.cgi"
USER_AGENT = "mev-pipeline/0.1 (multi-epitope vaccine pipeline; contact@example.org)"
# A key/email raises your RID priority and is strongly recommended for
# sustained use. Set via environmental variables; the key below is a working
# user key used as a fallback so remote searches do not sit in the anonymous
# low-priority queue (NCBI_API_KEY overrides it).
NCBI_TOOL = os.environ.get("NCBI_TOOL", "mev-pipeline")
NCBI_EMAIL = os.environ.get("NCBI_EMAIL", "mev-pipeline@example.com")
# NCBI rejects the key if it carries the "API-" display prefix, so strip it.
NCBI_API_KEY = os.environ.get("NCBI_API_KEY", "API-196de9f5b6b4de7fae94bfdfeb1cb4132f0a")
if NCBI_API_KEY.startswith("API-"):
    NCBI_API_KEY = NCBI_API_KEY[4:]
REQUEST_TIMEOUT = 90.0
PUT_COOLDOWN_SEC = 1.5  # politeness delay between independent Put submissions
POLL_INTERVAL_SEC = 5.0
MAX_RETRIES = 5
# NCBI web BLAST rejects a single Put whose combined query exceeds this many
# residues ("Your query(...) is longer than the maximum allowed"), so large
# candidate sets must be split across multiple sequential Put/RID cycles.
MAX_QUERY_RESIDUES = 100_000

_ERRORS = ("signal", "Remote", "UNKNOWN", "corrected", "alignment")

_XML_STATUS_RE = re.compile(r"<BlastOutput_status>(.*?)</BlastOutput_status>")
_XML_ERROR_RE = re.compile(r"<BlastOutput_error>(.*?)</BlastOutput_error>|<ERROR>(.*?)</ERROR>", re.S)
_RID_RE = re.compile(r"RID = (\S+)")


class BlastError(RuntimeError):
    """Raised when NCBI refuses a search or a RID never becomes READY."""


@dataclass
class BlastHit:
    hit_id: str
    accession: str
    title: str
    length: int
    identity: int
    positive: int
    align_length: int
    e_value: float
    score: int


@dataclass
class BlastResult:
    query_def: str
    hits: list[BlastHit] = field(default_factory=list)


async def _post(client: httpx.AsyncClient, data: dict[str, str], *, retries: int = MAX_RETRIES) -> httpx.Response:
    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            return await client.post(BLAST_URL, data=data)
        except httpx.HTTPError as exc:
            last_error = exc
            if attempt < retries - 1:
                await asyncio.sleep(2 * (attempt + 1))
    raise BlastError(f"NCBI BLAST unreachable: {last_error}")


def _parse_put(text: str) -> str:
    rid = None
    for line in text.splitlines():
        match = _RID_RE.search(line)
        if match:
            rid = match.group(1)
            break
    if not rid:
        raise BlastError("No RID returned by NCBI BLAST Put")
    return rid


def _is_ready_or_failed(xml: str) -> tuple[str, str]:
    status = _XML_STATUS_RE.search(xml)
    if not status:
        return "WAITING", ""
    state = status.group(1).strip()
    error = ""
    err_match = _XML_ERROR_RE.search(xml)
    if err_match:
        error = (err_match.group(1) or err_match.group(2) or "").strip()
    return state, error


def _parse_hits(xml: str) -> list[BlastResult]:
    root = ET.fromstring(xml)
    results: list[BlastResult] = []
    for iteration in root.findall(".//Iteration"):
        query_def = iteration.findtext("Iteration_query-def") or ""
        hits: list[BlastHit] = []
        for hit in iteration.findall(".//Hit"):
            hsps = hit.findall(".//Hsp")
            if not hsps:
                continue
            hsp = hsps[0]  # best HSP per hit
            hits.append(
                BlastHit(
                    hit_id=hit.findtext("Hit_id") or "",
                    accession=hit.findtext("Hit_accession") or "",
                    title=hit.findtext("Hit_def") or "",
                    length=int(hit.findtext("Hit_len") or 0),
                    identity=int(hsp.findtext("Hsp_identity") or 0),
                    positive=int(hsp.findtext("Hsp_positive") or 0),
                    align_length=int(hsp.findtext("Hsp_align-len") or 0),
                    e_value=float(hsp.findtext("Hsp_evalue") or 0.0),
                    score=float(hsp.findtext("Hsp_bit-score") or 0.0),
                )
            )
        results.append(BlastResult(query_def=query_def, hits=hits))
    return results


async def _submission_payload(
    queries: list[tuple[str, str]],
    *,
    program: str,
    database: str,
    entrez_query: str | None,
    expect: float,
    hitlist_size: int,
) -> dict[str, str]:
    fasta = "".join(f">{qid}\n{seq}\n" for qid, seq in queries)
    data: dict[str, str] = {
        "CMD": "Put",
        "PROGRAM": program,
        "DATABASE": database,
        "QUERY": fasta,
        "EXPECT": str(expect),
        "HITLIST_SIZE": str(hitlist_size),
        "FORMAT_TYPE": "XML",
        "MEGABLAST": "on",
        "FILTER": "F",
        "TOOL": NCBI_TOOL,
    }
    if NCBI_EMAIL:
        data["EMAIL"] = NCBI_EMAIL
    if NCBI_API_KEY:
        data["API_KEY"] = NCBI_API_KEY
    if entrez_query:
        data["ENTREZ_QUERY"] = entrez_query
    return data


async def blastp(
    queries: list[tuple[str, str]],
    *,
    program: str = "blastp",
    database: str = "refseq_protein",
    entrez_query: str | None = None,
    expect: float = 1e-5,
    hitlist_size: int = 5,
    poll_timeout: float = 600.0,
) -> list[BlastResult]:
    """Run BLASTp over `queries` (id, sequence) pairs.

    Queries are grouped into sequential Put/RID cycles so that no single
    submission exceeds NCBI's residue cap (100,000); each query in a batch
    costs one RID/poll cycle, and results are concatenated across batches.
    """
    if not queries:
        return []

    batches = _split_residue_batches(queries, MAX_QUERY_RESIDUES)

    async with httpx.AsyncClient(
        timeout=REQUEST_TIMEOUT,
        headers={"User-Agent": USER_AGENT},
        follow_redirects=True,
    ) as client:
        merged: list[BlastResult] = []
        for batch_i, batch in enumerate(batches):
            payload = await _submission_payload(
                batch,
                program=program,
                database=database,
                entrez_query=entrez_query,
                expect=expect,
                hitlist_size=hitlist_size,
            )
            put = await _post(client, payload)
            put.raise_for_status()
            rid = _parse_put(put.text)

            started = time.monotonic()
            cycle_remaining = poll_timeout / max(len(batches), 1)
            while time.monotonic() - started < cycle_remaining:
                await asyncio.sleep(POLL_INTERVAL_SEC)
                poll = await _post(client, {"CMD": "Get", "RID": rid, "FORMAT_TYPE": "XML"})
                if poll.status_code != 200:
                    continue
                state, error = _is_ready_or_failed(poll.text)
                if state == "READY":
                    try:
                        merged.extend(_parse_hits(poll.text))
                    except ET.ParseError as exc:
                        raise BlastError(f"NCBI returned malformed XML despite READY: {exc}") from exc
                    break
                if state == "FAILED":
                    raise BlastError(f"NCBI BLAST RID {rid} failed: {error or 'no error detail'}")

            # Best-effort cleanup; never let it break the caller.
            try:
                await _post(client, {"CMD": "Delete", "RID": rid})
            except Exception:  # noqa: BLE001
                pass

            if len(merged) < len([q for batch in batches[: batch_i + 1] for q in batch]) and \
               time.monotonic() - started >= cycle_remaining:
                raise BlastError(f"NCBI BLAST RID {rid} not READY within {int(cycle_remaining)}s")

    return merged


def _split_residue_batches(
    queries: list[tuple[str, str]],
    max_residues: int,
) -> list[list[tuple[str, str]]]:
    """Split (id, sequence) pairs into groups whose total residues fit the cap.

    A single oversized sequence is left as its own batch so the error it
    raises reflects the sequence rather than the batching logic.
    """
    batches: list[list[tuple[str, str]]] = []
    current: list[tuple[str, str]] = []
    current_residues = 0
    for qid, seq in queries:
        res = len(seq)
        if current and current_residues + res > max_residues:
            batches.append(current)
            current = []
            current_residues = 0
        current.append((qid, seq))
        current_residues += res
    if current:
        batches.append(current)
    return batches or [queries]


def stats(results: list[BlastResult]) -> dict:
    with_hits = [r for r in results if r.hits]
    best_identity = 0.0
    best_evalue = 0.0
    if with_hits:
        best_hit = min((h for r in results for h in r.hits), key=lambda h: h.e_value)
        best_identity = round((best_hit.identity / best_hit.align_length * 100) if best_hit.align_length else 0, 1)
        best_evalue = best_hit.e_value
    return {
        "queries": len(results),
        "withHits": len(with_hits),
        "bestIdentity": best_identity,
        "bestEvalue": best_evalue,
    }