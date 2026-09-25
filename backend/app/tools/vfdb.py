"""VFDB virulence-factor identification — Phase 3, step 3 ("Virulence Factor Check").

Candidates are BLASTp'd against the VFDB core dataset (setA: experimentally
verified VFs) to decide whether they are known or likely virulence factors.

The VFDB FASTA is downloaded once into a cache dir (default /tmp/mev-vfdb)
and turned into a local BLAST database with `makeblastdb`, then searched with
`blastp`.  If BLAST+ binaries are not installed locally, the search raises a
clear error so the caller can pause gracefully rather than fabricate results.

Classification rule (mirrors the pipeline's conservative thresholds):
a candidate is a virulence factor when its best VFDB hit has >= 30%
identity, e-value <= 1e-4, and a strict bit score > 100.
"""
from __future__ import annotations

import gzip
import os
import shutil
import subprocess
import tempfile
import urllib.request
from dataclasses import dataclass, field

VFDB_URL = "http://www.mgc.ac.cn/VFs/Down/VFDB_setA_pro.fas.gz"
VFDB_MIRROR = "https://zenodo.org/records/7511135/files/VFDB_setA_pro.fas.gz"
VFDB_CACHE_DIR = os.environ.get("MEV_VFDB_CACHE", os.path.join(tempfile.gettempdir(), "mev-vfdb"))
VFDB_FASTA_NAME = "VFDB_setA_pro.fas"
VFDB_IDENTITY_THRESHOLD = float(os.environ.get("VFDB_IDENTITY_THRESHOLD", "30.0"))
VFDB_EVALUE_THRESHOLD = float(os.environ.get("VFDB_EVALUE_THRESHOLD", "1e-4"))
VFDB_BITSCORE_THRESHOLD = float(os.environ.get("VFDB_BITSCORE_THRESHOLD", "100.0"))
_HTTP_TIMEOUT = 90.0


@dataclass
class VFDBHit:
    query_id: str
    subject_id: str
    subject_title: str
    identity: float
    evalue: float
    align_length: int
    bitscore: float = 0.0


@dataclass
class VFDBResult:
    query_id: str
    is_virulence_factor: bool = False
    best_identity: float = 0.0
    best_evalue: float = 1.0
    total_hits: int = 0
    best_hit: VFDBHit | None = None


@dataclass
class VFDBBatchResult:
    results: list[VFDBResult] = field(default_factory=list)
    total_candidates: int = 0
    virulence_count: int = 0
    vfdb_fasta_path: str | None = None


def _download(url: str, destination: str) -> None:
    os.makedirs(os.path.dirname(destination), exist_ok=True)
    request = urllib.request.Request(url, headers={"User-Agent": "mev-pipeline/0.1"})
    with urllib.request.urlopen(request, timeout=_HTTP_TIMEOUT) as response, open(destination, "wb") as out:
        shutil.copyfileobj(response, out)


def ensure_vfdb_fasta(cache_dir: str = VFDB_CACHE_DIR) -> str:
    """Return the path to the decompressed VFDB core FASTA, downloading once."""
    os.makedirs(cache_dir, exist_ok=True)
    fasta_path = os.path.join(cache_dir, VFDB_FASTA_NAME)
    if os.path.exists(fasta_path) and os.path.getsize(fasta_path) > 0:
        return fasta_path

    gz_path = os.path.join(cache_dir, VFDB_FASTA_NAME + ".gz")
    last_error: Exception | None = None
    for url in (VFDB_URL, VFDB_MIRROR):
        try:
            _download(url, gz_path)
            with gzip.open(gz_path, "rb") as gz, open(fasta_path, "wb") as out:
                shutil.copyfileobj(gz, out)
            os.remove(gz_path)
            return fasta_path
        except Exception as exc:  # noqa: BLE001 - try the mirror next
            last_error = exc
            continue
    raise RuntimeError(f"VFDB core dataset download failed: {last_error}")


def _have_blast() -> bool:
    return shutil.which("makeblastdb") is not None and shutil.which("blastp") is not None


def _build_db(fasta_path: str, cache_dir: str) -> str:
    db_name = os.path.join(cache_dir, "vfdb_core")
    if os.path.exists(db_name + ".phr") or os.path.exists(db_name + ".pdb"):
        return db_name
    subprocess.run(
        ["makeblastdb", "-in", fasta_path, "-dbtype", "prot", "-out", db_name],
        check=True,
        capture_output=True,
        text=True,
        errors="replace",
    )
    return db_name


def _run_blastp(query_fasta: str, db_name: str) -> list[VFDBHit]:
    proc = subprocess.run(
        [
            "blastp",
            "-query", query_fasta,
            "-db", db_name,
            "-outfmt", "6 qseqid sseqid pident length evalue bitscore stitle",
            "-evalue", str(VFDB_EVALUE_THRESHOLD),
            "-max_target_seqs", "5",
            "-num_threads", str(min(4, os.cpu_count() or 1)),
        ],
        check=True,
        capture_output=True,
    )
    hits: list[VFDBHit] = []
    for line in proc.stdout.decode("latin-1").splitlines():
        parts = line.split("\t")
        if len(parts) < 7:
            continue
        hits.append(
            VFDBHit(
                query_id=parts[0],
                subject_id=parts[1],
                subject_title=parts[6],
                identity=float(parts[2]),
                evalue=float(parts[4]),
                align_length=int(parts[3]),
                bitscore=float(parts[5]),
            )
        )
    return hits


def blast_vfdb(
    candidates: list[dict],
    *,
    identity_threshold: float = VFDB_IDENTITY_THRESHOLD,
    evalue_threshold: float = VFDB_EVALUE_THRESHOLD,
) -> VFDBBatchResult:
    """BLASTp candidates against the VFDB core dataset.

    Each candidate dict is expected to carry ``uniprotId``/``index`` and
    ``sequence``.  Returns per-candidate results plus aggregate counts.
    """
    if not candidates:
        return VFDBBatchResult(total_candidates=0)

    if not _have_blast():
        raise RuntimeError(
            "Local BLAST+ (makeblastdb/blastp) is not installed — cannot search VFDB. "
            "Install NCBI BLAST+ (https://ftp.ncbi.nlm.nih.gov/blast/executables/blast+/LATEST/) "
            "or set MEV_VFDB_CACHE to a pre-populated directory."
        )

    fasta_path = ensure_vfdb_fasta()
    db_name = _build_db(fasta_path, VFDB_CACHE_DIR)

    with tempfile.NamedTemporaryFile(mode="w", suffix=".fa", delete=False) as tmp:
        for i, cand in enumerate(candidates):
            seq = cand.get("sequence", "")
            if seq:
                qid = cand.get("uniprotId") or cand.get("name") or f"cand_{i}"
                tmp.write(f">{qid}\n{seq}\n")
        query_fasta = tmp.name

    try:
        hits = _run_blastp(query_fasta, db_name)
    finally:
        os.unlink(query_fasta)

    # Group hits by query id (header may carry "|uniprotId|name" decorations).
    by_query: dict[str, list[VFDBHit]] = {}
    for qid, hit in _expand_hits(hits, candidates):
        by_query.setdefault(qid, []).append(hit)

    results: list[VFDBResult] = []
    for i, cand in enumerate(candidates):
        qid = cand.get("uniprotId") or cand.get("name") or f"cand_{i}"
        qhits = by_query.get(qid, [])
        best = min(qhits, key=lambda h: h.evalue) if qhits else None
        best_identity = best.identity if best else 0.0
        is_vf = (
            best is not None
            and best.identity >= identity_threshold
            and best.evalue <= evalue_threshold
            and best.bitscore > VFDB_BITSCORE_THRESHOLD
        )
        results.append(
            VFDBResult(
                query_id=qid,
                is_virulence_factor=is_vf,
                best_identity=best_identity,
                best_evalue=(best.evalue if best else 1.0),
                total_hits=len(qhits),
                best_hit=best,
            )
        )

    return VFDBBatchResult(
        results=results,
        total_candidates=len(candidates),
        virulence_count=sum(1 for r in results if r.is_virulence_factor),
        vfdb_fasta_path=fasta_path,
    )


def _expand_hits(hits: list[VFDBHit], candidates: list[dict]) -> list[tuple[str, VFDBHit]]:
    """Map raw VFDB hits back onto candidate query ids.

    `blastp -outfmt 6` reports the exact query header, which may be the
    ``uniprotId`` we used as the header; also match by best guess from
    candidate names when present.
    """
    allowed = {(c.get("uniprotId") or c.get("name") or f"cand_{i}") for i, c in enumerate(candidates)}
    mapped: list[tuple[str, VFDBHit]] = []
    for hit in hits:
        qid = hit.query_id.split("|")[0] if hit.query_id else ""
        if qid in allowed:
            mapped.append((qid, hit))
    return mapped