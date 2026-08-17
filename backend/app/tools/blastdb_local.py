"""Local BLASTp against small reference databases — no external API.

Replaces the remote NCBI BLAST client for the three pipeline searches whose
reference sets are small enough to hold fully in memory:

- Phase 2-1: essential-gene proteins of S. agalactiae A909 (~309 proteins),
  fetched once from NCBI ``efetch`` keyed by the DEG GI identifiers and cached
  to disk.
- Phase 3-3: VFDB core dataset (delegated to ``vfdb.py``).
- Phase 3-4: reviewed human proteome (~20k proteins), fetched once from
  UniProt and cached to disk.

Each database is built with ``makeblastdb`` (one-time), then searched with
``blastp -outfmt 6``. Results are normalized into the same ``ncbiblast``
shapes the runners already consume (``BlastResult`` with ``query_def``), so
swapping the remote call for ``blastdb_local.blastp`` is transparent.
"""
from __future__ import annotations

import asyncio
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

import httpx

from . import ncbiblast

EMAIL = os.environ.get("NCBI_EMAIL", "mev-pipeline@example.com")
CACHE_DIR = os.environ.get("MEV_BLAST_DB_CACHE", os.path.join(tempfile.gettempdir(), "mev-blastdb"))
EFETCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
UNIPROT_STREAM_URL = "https://rest.uniprot.org/uniprotkb/stream"
REQUEST_TIMEOUT = 120.0
MAX_RETRIES = 3

DEG_DB_NAME = "deg_essential"
HUMAN_DB_NAME = "human_reviewed"


class LocalBlastError(RuntimeError):
    """Raised when local BLAST binaries are missing or a search fails."""


@dataclass
class LocalBlastCheck:
    available: bool
    blastp: str | None = None
    makeblastdb: str | None = None
    message: str = ""


def availability() -> LocalBlastCheck:
    """Report whether local BLAST+ binaries are on PATH."""
    blastp = shutil.which("blastp")
    makeblastdb = shutil.which("makeblastdb")
    if blastp and makeblastdb:
        return LocalBlastCheck(available=True, blastp=blastp, makeblastdb=makeblastdb)
    missing = [name for name, p in (("blastp", blastp), ("makeblastdb", makeblastdb)) if not p]
    return LocalBlastCheck(
        available=False,
        message=(
            "Local BLAST+ binaries not found: missing " + ", ".join(missing) + ". "
            "Install NCBI BLAST+ (brew install blast)."
        ),
    )


def require_local_blast() -> LocalBlastCheck:
    check = availability()
    if not check.available:
        raise LocalBlastError(check.message)
    return check


# ---------------------------------------------------------------------------
# Database provisioning
# ---------------------------------------------------------------------------
def db_path(name: str) -> str:
    return os.path.join(CACHE_DIR, name)


def db_exists(name: str) -> bool:
    base = db_path(name)
    return os.path.exists(base + ".phr") or os.path.exists(base + ".pdb")


async def _download(url: str, params: dict | None = None, *, destination: Path | None = None) -> str:
    async with httpx.AsyncClient(
        timeout=REQUEST_TIMEOUT,
        headers={"User-Agent": "mev-pipeline/0.1 (multi-epitope vaccine pipeline)"},
        follow_redirects=True,
    ) as client:
        last_error: Exception | None = None
        for attempt in range(MAX_RETRIES):
            try:
                response = await client.get(url, params=params)
                if response.status_code in (429, 500, 502, 503, 504) and attempt < MAX_RETRIES - 1:
                    await asyncio.sleep(1.5 * (2**attempt))
                    continue
                response.raise_for_status()
                if destination is not None:
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    destination.write_bytes(response.content)
                    return str(destination)
                return response.text
            except (httpx.HTTPStatusError, httpx.HTTPError) as exc:
                last_error = exc
                await asyncio.sleep(1.5 * (2**attempt))
    raise ConnectionError(f"download failed after {MAX_RETRIES} attempts: {last_error}")


async def ensure_deg_fasta(*, organism: str | None = None, all_organisms: bool = False) -> str:
    """Fetch the organism-wide DEG essential-protein FASTA.

    Uses NCBI ``efetch`` keyed by the DEG GI identifiers so the reference set
    exactly matches what ``deg.py`` reports as 'essential genes'.
    """
    from . import deg

    suffix = "_all" if all_organisms else ("" if not organism else "_" + _safe_name(organism))
    fasta_path = os.path.join(CACHE_DIR, DEG_DB_NAME + suffix + ".fasta")
    if os.path.exists(fasta_path) and os.path.getsize(fasta_path) > 0:
        return fasta_path

    genes = await deg.fetch_essential_genes(organism=None if all_organisms else organism)
    uids = deg.essential_uids(genes)
    if not uids:
        raise RuntimeError("DEG reference contains no essential-gene UIDs.")

    # efetch accepts up to ~200 ids per query; 309 genes -> 2 calls.
    chunks = [uids[i : i + 200] for i in range(0, len(uids), 200)]
    parts: list[str] = []
    for chunk in chunks:
        id_query = ",".join(str(u) for u in chunk)
        params: dict[str, str] = {
            "db": "protein",
            "id": id_query,
            "rettype": "fasta",
            "retmode": "text",
            "email": EMAIL,
            "api_key": ncbiblast.NCBI_API_KEY,
        }
        fasta = await _download(EFETCH_URL, params)
        parts.append(fasta)
        await asyncio.sleep(1.0)

    text = "".join(parts)
    if ">" not in text:
        raise RuntimeError("efetch returned no protein FASTA for DEG essential-gene GIs.")

    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(fasta_path, "w") as out:
        out.write(text)
    return fasta_path


async def ensure_human_fasta() -> str:
    """Fetch the reviewed human proteome from UniProt, one time."""
    fasta_path = os.path.join(CACHE_DIR, HUMAN_DB_NAME + ".fasta")
    if os.path.exists(fasta_path) and os.path.getsize(fasta_path) > 0:
        return fasta_path

    text = await _download(
        UNIPROT_STREAM_URL,
        {"query": "taxonomy_id:9606 AND reviewed:true", "format": "fasta"},
    )
    if ">" not in text:
        raise RuntimeError("UniProt returned no reviewed human proteome FASTA.")

    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(fasta_path, "w") as out:
        out.write(text)
    return fasta_path


def _build_db(fasta_path: str, name: str) -> str:
    out = db_path(name)
    if db_exists(name):
        return out
    os.makedirs(CACHE_DIR, exist_ok=True)
    proc = subprocess.run(
        ["makeblastdb", "-in", fasta_path, "-dbtype", "prot", "-out", out],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise LocalBlastError(f"makeblastdb failed for {name}: {proc.stderr[:400]}")
    return out


def _safe_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


async def ensure_deg_db(*, organism: str | None = None) -> str:
    name = f"{DEG_DB_NAME}_all" if organism is None else f"{DEG_DB_NAME}_{_safe_name(organism)}"
    if db_exists(name):
        return db_path(name)
    require_local_blast()
    fasta = await ensure_deg_fasta(organism=organism, all_organisms=organism is None)
    return _build_db(fasta, name)


async def ensure_human_db() -> str:
    if db_exists(HUMAN_DB_NAME):
        return db_path(HUMAN_DB_NAME)
    require_local_blast()
    fasta = await ensure_human_fasta()
    return _build_db(fasta, HUMAN_DB_NAME)


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------
def blastp_sync(
    queries: list[tuple[str, str]],
    database: str,
    *,
    expect: float = 1e-5,
    hitlist_size: int = 5,
) -> list[ncbiblast.BlastResult]:
    """Local BLASTp of `queries` against `database` (db basename in cache dir).

    Returns the same ``ncbiblast.BlastResult`` list the remote client returns,
    so ``runner.py``/``runner_additions.py`` need no reshaping.
    """
    require_local_blast()
    db = db_path(database)
    if not db_exists(database):
        raise LocalBlastError(f"local BLAST database '{database}' not built yet")

    with tempfile.NamedTemporaryFile(mode="w", suffix=".fa", delete=False) as tmp:
        for qid, seq in queries:
            if seq:
                tmp.write(f">{qid}\n{seq}\n")
        query_fasta = tmp.name

    try:
        proc = subprocess.run(
            [
                "blastp",
                "-query", query_fasta,
                "-db", db,
                "-outfmt", "6 qseqid sseqid nident pident length slen evalue bitscore stitle sallseqid",
                "-evalue", str(expect),
                "-max_target_seqs", str(hitlist_size),
                "-num_threads", str(min(4, os.cpu_count() or 1)),
            ],
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            raise LocalBlastError(f"blastp failed: {proc.stderr[:400]}")
    finally:
        os.unlink(query_fasta)

    results: dict[str, ncbiblast.BlastResult] = {}
    for line in proc.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) < 10:
            continue
        qid, sseqid, sallseqid = parts[0], parts[1], parts[9]
        # nident (col 3) is the raw count of identical residues, matching the
        # remote client's Hsp_identity; length (col 5) is the alignment span.
        nident = int(float(parts[2]))
        align_length = int(float(parts[4]))
        accession = (sseqid or sallseqid or "").split("|")[-1]
        hit = ncbiblast.BlastHit(
            hit_id=sseqid,
            accession=accession,
            title=parts[8] or sseqid,
            length=int(float(parts[5])),
            identity=nident,
            positive=nident,
            align_length=align_length,
            e_value=float(parts[6]),
            score=float(parts[7]),
        )
        result = results.setdefault(qid, ncbiblast.BlastResult(query_def=qid))
        result.hits.append(hit)

    # Preserve caller query order; a query with no hits still gets a result.
    ordered: list[ncbiblast.BlastResult] = []
    for qid, _seq in queries:
        if qid in results:
            ordered.append(results[qid])
        else:
            ordered.append(ncbiblast.BlastResult(query_def=qid))
    return ordered


async def blastp(
    queries: list[tuple[str, str]],
    *,
    database: str,
    expect: float = 1e-5,
    hitlist_size: int = 5,
) -> list[ncbiblast.BlastResult]:
    """Async wrapper around ``blastp_sync`` for the runner interface."""
    return await asyncio.get_running_loop().run_in_executor(
        None,
        lambda: blastp_sync(
            queries,
            database,
            expect=expect,
            hitlist_size=hitlist_size,
        ),
    )


async def blastp_with_remote_fallback(
    queries: list[tuple[str, str]],
    *,
    database: str,
    remote_database: str,
    entrez_query: str | None = None,
    expect: float = 1e-5,
    hitlist_size: int = 5,
) -> tuple[list[ncbiblast.BlastResult], str]:
    """Prefer the local BLAST DB; fall back to remote NCBI when it cannot run.

    Falls back (instead of raising) only when local BLAST is unavailable or
    the local search itself fails, so a missing install never blocks the
    pipeline. Returns ``(results, source_label)``.
    """
    try:
        require_local_blast()
        if not db_exists(database):
            raise LocalBlastError(f"local BLAST database '{database}' not built")
        return (
            await blastp(queries, database=database, expect=expect, hitlist_size=hitlist_size),
            f"BLASTp (local: {database})",
        )
    except LocalBlastError:
        results = await ncbiblast.blastp(
            queries,
            program="blastp",
            database=remote_database,
            entrez_query=entrez_query,
            expect=expect,
            hitlist_size=hitlist_size,
        )
        return results, f"BLASTp (NCBI remote: {remote_database})"
