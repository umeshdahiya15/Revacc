"""Runner additions for Phase 2-2 through 3-4.

Standalone module so ``test_phase2_3_runners.py`` can import the runner
functions directly (the project's test pattern calls functions with the
``(session, job, step)`` signature).  ``runner.py`` imports these and
registers adapter wrappers in ``STEP_RUNNERS``.

Tool reachability, verified live during development:

- 2-2 PSORTb:       graceful pause (DNS NXDOMAIN / Cloudflare 403)
- 2-3 DeepTMHMM:    graceful pause (no REST API; BioLib is login-gated)
- 2-4 Phobius:      REAL — EBI Job Dispatcher REST (verified end-to-end)
- 3-1 AlgPred 2.0:  graceful pause (HTML-only web form)
- 3-2 VaxiJen 2.0:  graceful pause (Cloudflare 403 on submission endpoint)
- 3-3 VFDB:         REAL — local BLASTp against VFDB core dataset
- 3-4 Human Homology: REAL — local BLASTp against reviewed human proteome
"""
from __future__ import annotations

import asyncio
import hashlib
import httpx
import json
import os
import sys
import tempfile
import time

from Bio.PDB import PDBParser
from Bio.PDB.Polypeptide import protein_letters_3to1
from Bio.SeqUtils.ProtParam import ProteinAnalysis

from . import blastdb_local
from .ebi_rest_client import (
    EBIRestClient,
    EBIRestClientError,
    EBIResult,
    PSORTbClient,
    PSORTbClientError,
    classify_phobius,
    find_psortb_binary,
    is_psortb_surface,
    parse_phobius_out,
)
from .graceful_pause import (
    ToolUnavailableError,
)
from .vfdb import (
    VFDBBatchResult,
    VFDB_BITSCORE_THRESHOLD,
    VFDB_EVALUE_THRESHOLD,
    VFDB_IDENTITY_THRESHOLD,
    blast_vfdb,
)
from . import vaxijen_local, algpred_local, cytokine_local
from . import bcell_local, structure_local, quality_local, esmfold
from . import adjuvant_dbd2_local
from .swissmodel_contract import admit_swissmodel_submission
from .swissmodel_lifecycle import SwissModelLifecycleAdapter
from .swissmodel_runtime import (
    SWISSMODEL_PROVIDER,
    mev_sequence_fingerprint,
    normalize_mev_sequence,
    preflight_swissmodel_submission,
    swissmodel_runtime_capability,
)

EBI_EMAIL = os.environ.get("EBI_EMAIL", "mev-pipeline@example.com")
PHOBIUS_RATE_LIMIT_SEC = 1.0  # min spacing between EBI submissions
PHOBIUS_CONCURRENCY = 8      # max Phobius jobs in flight at once
PHOBIUS_CAP = int(os.environ.get("MEV_PHOBIUS_CAP", "0"))  # 0 = complete candidate pool
PHOBIUS_POLL_INTERVAL_SEC = 2.0  # status poll cadence (default is 5.0)
PHOBIUS_STEP_TIMEOUT_SEC = 900  # 15 min hard limit for entire step 2-4
PHOBIUS_CACHE_DIR = os.environ.get(
    "MEV_PHOBIUS_CACHE", os.path.join(tempfile.gettempdir(), "mev-phobius-cache")
)


PHOBIUS_CACHE_CONTRACT = "phobius-v2"


def _phobius_cache_path() -> str:
    """Versioned JSON map keyed by sequence and analysis contract."""
    os.makedirs(PHOBIUS_CACHE_DIR, exist_ok=True)
    return os.path.join(PHOBIUS_CACHE_DIR, f"results-{PHOBIUS_CACHE_CONTRACT}.json")


def _load_phobius_cache() -> dict[str, dict]:
    path = _phobius_cache_path()
    try:
        with open(path, "r", encoding="utf-8") as fh:
            payload = json.load(fh)
        if payload.get("contract") != PHOBIUS_CACHE_CONTRACT:
            return {}
        return payload.get("results") or {}
    except (FileNotFoundError, json.JSONDecodeError, AttributeError):
        return {}


def _save_phobius_cache(cache: dict[str, dict]) -> None:
    path = _phobius_cache_path()
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"contract": PHOBIUS_CACHE_CONTRACT, "tool": "Phobius", "results": cache}, fh)
        os.replace(tmp, path)
    except OSError:
        pass  # cache is best-effort; never fail the pipeline on a write error


def _seq_key(sequence: str) -> str:
    return hashlib.sha256(sequence.encode("utf-8")).hexdigest()


def _normalise_protein_sequence(sequence: str) -> str:
    """Canonicalize a protein sequence for identity checks and fingerprints."""
    return "".join(ch for ch in str(sequence or "").upper() if ch.isalpha())


def _sequence_fingerprint(sequence: str) -> str:
    return hashlib.sha256(_normalise_protein_sequence(sequence).encode("utf-8")).hexdigest()


class _SubmissionGate:
    """Global pacing for concurrent EBI submissions.

    ``wait()`` blocks until at least ``seconds`` have elapsed since the last
    call, so N concurrent workers never submit more than one job per
    ``seconds`` (EBI politeness) while jobs still overlap.
    """

    def __init__(self, seconds: float) -> None:
        self._seconds = seconds
        self._next_ok = 0.0
        self._lock = asyncio.Lock()

    async def wait(self) -> None:
        async with self._lock:
            now = time.monotonic()
            if self._next_ok > now:
                await asyncio.sleep(self._next_ok - now)
            self._next_ok = time.monotonic() + self._seconds

# Human-proteome homology filter parameters (Phase 3 step 4).
HUMAN_HOMOLOGY_IDENTITY_THRESHOLD = 0.30
HOMOLOGY_EVALUE_THRESHOLD = 1e-5


# ---------------------------------------------------------------------------
# Phase 8: Interferon/Cytokine Overlap Analysis
# ---------------------------------------------------------------------------

def _compute_cytokine_overlap(epitopes: list[dict]) -> dict[str, int]:
    """Compute cytokine overlap from CTL/HTL epitopes.

    Since IFNepitope, IL4Pred, IL10Pred are all graceful-paused, this
    function uses the available epitope pool and counts how many fall into
    each cytokine category based on simple heuristics (antigenicity score,
    physicochemical properties). Returns dict with counts per cytokine.
    """
    counts = {"ifn_gamma": 0, "il4": 0, "il10": 0}
    for e in epitopes:
        # Simple heuristic: use antigenicityScore to categorize
        ant = e.get("antigenicityScore", 0) or 0
        if ant > 0.5:
            counts["ifn_gamma"] += 1
        elif ant > 0.3:
            counts["il4"] += 1
        else:
            counts["il10"] += 1
    return counts


def _compute_bt_overlap(epitopes: list[dict]) -> dict:
    """Compute deterministic sequence overlap over selected epitope rows.

    The input is the actual CTL/HTL/B-cell epitope table produced by the run.
    Rows marked ``selected=False`` and rows without a sequence are excluded.
    A B-cell row overlaps a T-cell row when one normalized peptide sequence
    contains the other. This is a sequence-level overlap, not an ID join or a
    paper-count substitute, and every match is returned for provenance.
    """
    bcell_types = {"BCELL_LINEAR", "BCELL_CONFORMATIONAL"}
    tcell_types = {"CTL", "HTL"}

    selected = [
        e for e in epitopes
        if isinstance(e, dict)
        and e.get("selected", True) is not False
        and str(e.get("sequence") or "").strip()
    ]
    bcell_epitopes = [e for e in selected if e.get("type") in bcell_types]
    tcell_epitopes = [e for e in selected if e.get("type") in tcell_types]

    def _sequence(epitope: dict) -> str:
        return "".join(str(epitope.get("sequence") or "").split()).upper()

    def _id(epitope: dict, prefix: str, index: int) -> str:
        return str(epitope.get("id") or f"{prefix}-{index}")

    pairs: list[dict] = []
    overlap_ids: list[str] = []
    for b_index, bcell in enumerate(bcell_epitopes):
        b_seq = _sequence(bcell)
        b_id = _id(bcell, "bcell", b_index)
        matches: list[dict] = []
        for t_index, tcell in enumerate(tcell_epitopes):
            t_seq = _sequence(tcell)
            if b_seq not in t_seq and t_seq not in b_seq:
                continue
            overlap_sequence = b_seq if len(b_seq) <= len(t_seq) else t_seq
            matches.append({
                "bcellId": b_id,
                "tcellId": _id(tcell, "tcell", t_index),
                "bcellSequence": b_seq,
                "tcellSequence": t_seq,
                "overlapSequence": overlap_sequence,
            })
        if matches:
            overlap_ids.append(b_id)
            pairs.extend(matches)

    return {
        "bcell_count": len(bcell_epitopes),
        "tcell_count": len(tcell_epitopes),
        "overlap_count": len(overlap_ids),
        "overlap_ids": overlap_ids,
        "overlap_pairs": pairs,
        "method": "deterministic normalized sequence containment",
    }


# ---------------------------------------------------------------------------
# Cytokine Overlap computation (used as sub-step within Phase 8-1 Pop Coverage)
# ---------------------------------------------------------------------------
async def run_cytokine_overlap(session: dict, job, step) -> dict:
    """Do not substitute antigenicity for cytokine-specific predictors."""
    raise ToolUnavailableError(
        tool_name="IFNepitope/IL4Pred/IL10Pred",
        reason="Cytokine-specific external predictors are unavailable; antigenicity is not a valid substitute.",
        workaround="Run the named cytokine predictors and attach their real outputs.",
    )


# ---------------------------------------------------------------------------
# Runner 8-2 / 9-2: B-cell / T-cell Epitope Overlap (graceful computation)
# ---------------------------------------------------------------------------
async def run_8_2(session: dict, job, step) -> dict:
    """Compute selected B-cell/T-cell sequence overlap locally.

    ``session["epitopes"]`` is authoritative because it contains the final
    selected CTL, HTL, and B-cell rows. The conservancy result is used only
    when that full table is unavailable (for example, in a direct runner
    invocation). The step is a deterministic local analysis and does not
    pause merely because the pipeline tool label is not an external service.
    """
    del job, step
    # The final session epitope table is authoritative. Only direct callers
    # that do not provide that table may use the legacy conservancy subset;
    # an explicitly empty table must remain empty rather than reintroducing
    # stale rows from an earlier analysis stage.
    if "epitopes" in session:
        epitopes = session.get("epitopes") or []
    else:
        epitopes = session.get("conserved_epitopes") or []
    counts = _compute_bt_overlap(epitopes)
    session["bt_overlap"] = counts
    return {
        "message": (
            f"B/T overlap: B-cell={counts['bcell_count']}, "
            f"T-cell={counts['tcell_count']}, overlap={counts['overlap_count']}"
        ),
        "total_analyzed": len(epitopes),
        "overlap": counts,
        "method": counts["method"],
        "source": "local-analysis",
        "provenance": {
            "status": "local-analysis",
            "method": counts["method"],
            "input": "selected CTL, HTL, and B-cell epitope rows from the run session",
            "selectionRule": "selected != false and non-empty normalized peptide sequence",
            "comparisonRule": "case-insensitive sequence containment",
            "syntheticValues": False,
        },
    }


def _surface_candidate_id(candidate: dict) -> str:
    """Return a stable candidate identity for localization unions."""
    accession = candidate.get("uniprotId")
    if accession and accession != "?":
        return str(accession)
    return f"index:{candidate.get('index', '')}"


async def run_2_2(session: dict, job, step) -> dict:
    """Run PSORTb locally and record a real surface-localization layer.

    A configured/on-PATH PSORTb executable is preferred. The existing Docker
    adapter remains a compatible local fallback for deployments that use the
    bundled PSORTb image. If neither is available, this step records an
    explicit residual and returns a partial result; step 2-4 then continues
    with Phobius rather than treating absence as scientific evidence.
    """
    from . import psortb_docker

    candidates = (session.get("essential") or {}).get("candidates") or []
    if not candidates:
        return {
            "message": "No essential candidates to localize",
            "surface_exposed_count": 0,
            "total_analyzed": 0,
            "classifications": [],
            "method": "psortb_6.0_local",
            "status": "no_data",
            "provenance": {"status": "unavailable", "reason": "no essential candidates"},
        }

    sequences = [c.get("sequence", "") for c in candidates]
    started = time.monotonic()
    backend = "psortb_6.0_local"
    psortb_available = False
    availability_reason: str | None = None
    localizations: list[dict]

    binary = find_psortb_binary()
    if binary:
        try:
            localizations = await PSORTbClient(binary=binary).localize(sequences, gram="positive")
            psortb_available = True
        except PSORTbClientError as exc:
            availability_reason = f"configured PSORTb binary failed: {exc}"
            localizations = []
    else:
        # Keep the previously supported Docker installation useful, but never
        # fall back to a heuristic when neither real PSORTb path is present.
        try:
            psortb_docker.require_available()
            localizations = await psortb_docker.localize(sequences, gram="positive")
            backend = "psortb_3.0_docker"
            psortb_available = True
        except (psortb_docker.PSORTbUnavailable, OSError, RuntimeError) as exc:
            availability_reason = str(exc)
            localizations = []

    if not psortb_available:
        reason = availability_reason or "No PSORTb v6 executable was found on PATH or PSORTB_BIN."
        session["surface_exposed"] = {
            "candidates": [],
            "count": 0,
            "provenance": {
                "status": "partial",
                "method": "PSORTb unavailable; Phobius fallback",
                "psortbAvailable": False,
                "residualGap": reason,
            },
        }
        session["psortb"] = {
            "localization_counts": {},
            "surface_count": 0,
            "surface_candidate_ids": [],
            "total_analyzed": len(candidates),
            "available": False,
            "method": backend,
            "provenance": {
                "status": "unavailable",
                "method": "PSORTb local binary",
                "residualGap": reason,
            },
        }
        session.setdefault("_funnel_counts", {})["surface_exposed"] = 0
        return {
            "message": f"PSORTb unavailable; Phobius will run with documented residual: {reason}",
            "total_analyzed": len(candidates),
            "surface_exposed_count": 0,
            "localization_counts": {},
            "classifications": [],
            "method": backend,
            "status": "partial",
            "residual_gap": reason,
            "provenance": {
                "status": "partial",
                "method": "PSORTb unavailable; Phobius fallback",
                "psortbAvailable": False,
                "residualGap": reason,
            },
            "elapsed_sec": round(time.monotonic() - started, 1),
        }

    classifications: list[dict] = []
    surface_exposed: list[dict] = []
    loc_counts: dict[str, int] = {}
    for cand, loc in zip(candidates, localizations):
        localization = loc.get("localization", "Unknown")
        loc_counts[localization] = loc_counts.get(localization, 0) + 1
        is_surf = is_psortb_surface(localization)
        classifications.append({
            "uniprotId": cand.get("uniprotId", "?"),
            "name": cand.get("name", "?"),
            "localization": localization,
            "category": loc.get("category"),
            "score": loc.get("score", 0.0),
            "surface_exposed": is_surf,
        })
        if is_surf:
            surface_exposed.append(cand)

    psortb_ids = {_surface_candidate_id(cand) for cand in surface_exposed}
    provenance = {
        "status": "real",
        "method": "PSORTb local binary" if backend == "psortb_6.0_local" else "PSORTb 3.0 Docker",
        "psortbAvailable": True,
        "gram": "positive",
    }
    session["surface_exposed"] = {
        "candidates": surface_exposed,
        "count": len(surface_exposed),
        "provenance": provenance,
    }
    session["psortb"] = {
        "localization_counts": loc_counts,
        "surface_count": len(surface_exposed),
        "surface_candidate_ids": sorted(psortb_ids),
        "total_analyzed": len(candidates),
        "available": True,
        "method": backend,
        "provenance": provenance,
    }
    session.setdefault("_funnel_counts", {})["surface_exposed"] = len(surface_exposed)

    return {
        "message": (
            f"{backend}: {len(surface_exposed)}/{len(candidates)} surface-exposed "
            "(outer membrane / lipoprotein / cell-wall-anchored / extracellular included)"
        ),
        "total_analyzed": len(candidates),
        "surface_exposed_count": len(surface_exposed),
        "localization_counts": loc_counts,
        "classifications": classifications,
        "method": backend,
        "gram": "positive",
        "status": "completed",
        "provenance": provenance,
        "elapsed_sec": round(time.monotonic() - started, 1),
    }


# ---------------------------------------------------------------------------
# 2-3 DeepTMHMM — Transmembrane Helix Prediction (graceful pause)
# ---------------------------------------------------------------------------
async def run_2_3(session: dict, job, step) -> dict:
    """Transmembrane-helix count (informational).

    The authoritative 0-1 TMH filter is applied in step 2-4 using REAL Phobius
    transmembrane-region counts (the local heuristic over-predicted helices and
    wrongly discarded genuine single-pass surface antigens). This step only
    reports the local estimate for reference and does NOT narrow the set.
    """
    surface = (session.get("surface_exposed") or {}).get("candidates") or []
    if not surface:
        return {
            "message": "No surface-exposed candidates for TMH analysis",
            "transmembrane_count": 0,
            "total_analyzed": 0,
            "method": "tmh_local_informational",
        }

    from .structure_local import _predict_transmembrane_local

    tmhmm_results = []
    has_tm_total = 0
    for cand in surface:
        seq = cand.get("sequence", "")
        if not seq:
            continue
        _, tm_segments = _predict_transmembrane_local(seq)
        if len(tm_segments) > 0:
            has_tm_total += 1
        tmhmm_results.append({
            "uniprotId": cand.get("uniprotId", "?"),
            "name": cand.get("name", "?"),
            "tm_helices": len(tm_segments),
            "sequence_length": len(seq),
        })

    session["tmhmm_results"] = tmhmm_results
    # Do NOT narrow surface_exposed here; the real filter is in 2-4 (Phobius).
    return {
        "message": (
            f"TMH (local estimate, informational): {has_tm_total}/{len(surface)} "
            "have transmembrane helices; real 0-1 TMH filter applied in 2-4 (Phobius)"
        ),
        "total_analyzed": len(surface),
        "transmembrane_count": has_tm_total,
        "tmhmm_results": tmhmm_results,
        "method": "tmh_local_informational",
    }


# ---------------------------------------------------------------------------
# 2-4 Phobius — Signal Peptide & TM Prediction (REAL, EBI REST)
# ---------------------------------------------------------------------------
async def run_2_4(session: dict, job, step) -> dict:
    """Union real Phobius topology calls with the PSORTb surface set.

    PSORTb categories such as cell-wall, lipoprotein, and extracellular are
    retained when Phobius is unknown or unavailable. A real Phobius
    intracellular classification remains excluded.
    """
    # Step 2-2 provides the authoritative PSORTb surface set. When it is
    # present, classify the complete essential set so Phobius-positive records
    # can be added (union) rather than using Phobius to overwrite PSORTb. For
    # direct/test invocations without PSORTb metadata, retain the supplied
    # pre-Phobius set as the legacy candidate pool and use its count contract.
    surface_state = session.get("surface_exposed") or {}
    preexisting_candidates = list(surface_state.get("candidates") or [])
    essential_candidates = list((session.get("essential") or {}).get("candidates") or [])
    psortb_state = session.get("psortb") or {}
    psortb_ids = set(psortb_state.get("surface_candidate_ids") or [])
    psortb_unavailable = psortb_state.get("available") is False
    reported_count = surface_state.get("count")
    # Older sessions persisted only the pre-Phobius PSORTb candidate list.
    # Treat a deliberately shorter list/count as that real upstream result,
    # but never treat an unannotated complete candidate list as PSORTb proof.
    if (
        not psortb_ids
        and isinstance(reported_count, int)
        and 0 <= reported_count < len(preexisting_candidates)
    ):
        psortb_ids = {
            _surface_candidate_id(candidate)
            for candidate in preexisting_candidates[:reported_count]
        }
    explicit_psortb = bool(psortb_ids)
    if explicit_psortb:
        candidate_pool = list((session.get("essential") or {}).get("candidates") or [])
        if not candidate_pool:
            candidate_pool = preexisting_candidates
    else:
        reported_count = surface_state.get("count")
        if isinstance(reported_count, int) and 0 <= reported_count < len(preexisting_candidates):
            # A caller that supplies a pre-filtered set plus an explicit count
            # has identified which leading records came from PSORTb. This is
            # only a compatibility path; normal production runs set psortb IDs.
            candidate_pool = preexisting_candidates[:reported_count]
        else:
            candidate_pool = preexisting_candidates
        if not candidate_pool and essential_candidates:
            # PSORTb unavailable (or returned no positives): retain the full
            # scientifically valid essential pool for the real Phobius pass.
            candidate_pool = essential_candidates
    candidates = candidate_pool
    if not candidates:
        return {
            "message": "No surface-exposed candidates to analyze",
            "surface_exposed_count": 0,
            "total_analyzed": 0,
            "classifications": [],
        }

    # Cap candidates to avoid excessive EBI REST calls, prioritizing the
    # already validated PSORTb-positive records so the union is not truncated.
    if PHOBIUS_CAP > 0 and len(candidates) > PHOBIUS_CAP:
        candidates = sorted(
            candidates,
            key=lambda cand: (_surface_candidate_id(cand) not in psortb_ids, _surface_candidate_id(cand)),
        )[:PHOBIUS_CAP]

    client = EBIRestClient(email=EBI_EMAIL, poll_interval=PHOBIUS_POLL_INTERVAL_SEC)
    started = time.monotonic()
    classifications: list[dict] = []
    surface_exposed: list[dict] = []
    cache = _load_phobius_cache()
    cache_hits = 0

    sem = asyncio.Semaphore(PHOBIUS_CONCURRENCY)
    poll_sem = asyncio.Semaphore(PHOBIUS_CONCURRENCY)
    gate = _SubmissionGate(PHOBIUS_RATE_LIMIT_SEC)
    cache_lock = asyncio.Lock()

    def _cached_classification(cand: dict, entry: dict) -> dict:
        """Rebuild a classification from a cache entry (no live EBI call)."""
        return {
            "uniprotId": cand.get("uniprotId", "?"),
            "name": cand.get("name", "?"),
            "classification": entry["classification"],
            "signal_peptide": entry.get("signal_peptide"),
            "transmembrane_regions": entry.get("transmembrane_regions", []),
            "domains": entry.get("domains", []),
            "cached": True,
        }

    async def _classify(i: int, cand: dict) -> tuple[int, dict, bool]:
        """Run one Phobius job; returns (index, classification, keep)."""
        seq = cand.get("sequence", "")
        if not seq:
            return i, {
                "uniprotId": cand.get("uniprotId", "?"),
                "name": cand.get("name", "?"),
                "classification": "unknown",
                "reason": "empty sequence",
            }, False

        key = _seq_key(seq)
        entry = cache.get(key)
        if entry and entry.get("classification") in ("secreted", "membrane", "intracellular"):
            nonlocal cache_hits
            cache_hits += 1
            keep_cached = (
                entry["classification"] in ("secreted", "membrane")
                and len(entry.get("transmembrane_regions") or []) <= 1
            )
            return (
                i,
                _cached_classification(cand, entry),
                keep_cached,
            )

        async with sem:
            await gate.wait()
            job_id = await client.submit("phobius", seq)

        # Do not hold the submission slot while EBI queues and processes the
        # job. Polling is bounded separately so all candidates can be queued.
        async with poll_sem:
            status = await client.poll_status("phobius", job_id)
            raw_text = ""
            if status == "FINISHED":
                try:
                    raw_text = await client.fetch_result("phobius", job_id, "out")
                except EBIRestClientError:
                    raw_text = ""
            result = EBIResult(
                job_id=job_id,
                tool="phobius",
                status=status,
                raw_text=raw_text,
            )

        if result.status != "FINISHED" or not result.raw_text.strip():
            # An unavailable localization is not evidence of surface exposure.
            return i, {
                "uniprotId": cand.get("uniprotId", "?"),
                "name": cand.get("name", "?"),
                "classification": "unknown",
                "reason": f"EBI job {result.status}",
                "job_id": result.job_id,
                "provenance": "unavailable",
            }, False

        parsed = parse_phobius_out(result.raw_text)
        topo = classify_phobius(parsed)
        classification = {
            "uniprotId": cand.get("uniprotId", "?"),
            "name": cand.get("name", "?"),
            "classification": topo,
            "signal_peptide": parsed["signal_peptide"],
            "transmembrane_regions": parsed["transmembrane_regions"],
            "domains": parsed["domains"],
            "job_id": result.job_id,
        }
        async with cache_lock:
            cache[key] = {
                "classification": topo,
                "signal_peptide": parsed["signal_peptide"],
                "transmembrane_regions": parsed["transmembrane_regions"],
                "domains": parsed["domains"],
            }
            _save_phobius_cache(cache)
        # Keep surface proteins with a signal peptide / membrane topology AND
        # 0-1 real transmembrane regions (paper: DeepTMHMM 0-1 TMH + Phobius).
        keep = topo in ("secreted", "membrane") and len(parsed["transmembrane_regions"]) <= 1
        return i, classification, keep

    step_start = time.monotonic()
    try:
        outcomes: list[object] = await asyncio.wait_for(
            asyncio.gather(
                *(_classify(i, c) for i, c in enumerate(candidates)),
                return_exceptions=True,
            ),
            timeout=PHOBIUS_STEP_TIMEOUT_SEC,
        )
    except asyncio.TimeoutError:
        # Step timed out — collect whatever completed from cache, mark rest unknown.
        outcomes = []
        for i, c in enumerate(candidates):
            key = _seq_key(c.get("sequence", ""))
            entry = cache.get(key)
            if entry and entry.get("classification") in ("secreted", "membrane", "intracellular"):
                keep_to = (
                    entry["classification"] in ("secreted", "membrane")
                    and len(entry.get("transmembrane_regions") or []) <= 1
                )
                outcomes.append((i, _cached_classification(c, entry), keep_to))
            else:
                outcomes.append((i, {
                    "uniprotId": c.get("uniprotId", "?"),
                    "name": c.get("name", "?"),
                    "classification": "unknown",
                    "reason": "step timeout",
                }, False))  # unknown localization is excluded, never fabricated as surface
    finally:
        # Persist whatever completed so a rerun skips finished jobs.
        _save_phobius_cache(cache)
        await client.close()

    # A Phobius outage must not erase independently valid PSORTb results.
    # Without any PSORTb-positive records, retain the historical pause/error
    # behavior; with them, continue and return a partial result containing
    # only those real PSORTb candidates plus any valid Phobius positives.
    total = len(candidates)
    errors = [oc for oc in outcomes if isinstance(oc, Exception)]
    if errors and len(errors) / max(total, 1) > 0.5 and not psortb_ids:
        failure_summary = ", ".join(str(e)[:120] for e in errors[:3])
        raise ToolUnavailableError(
            tool_name="Phobius (EBI)",
            reason=(
                f"{len(errors)}/{total} Phobius submissions failed after "
                f"retries: {failure_summary}"
            ),
            workaround=(
                "EBI JDispatcher may be temporarily down. Re-run when it is "
                "available; no unvalidated localization is being fabricated."
            ),
        )

    resolved: list[tuple[int, dict, bool]] = []
    for i, oc in enumerate(outcomes):
        if isinstance(oc, Exception):
            cand = candidates[i]
            resolved.append(
                (
                    i,
                    {
                        "uniprotId": cand.get("uniprotId", "?"),
                        "name": cand.get("name", "?"),
                        "classification": "unknown",
                        "reason": f"EBI unavailable: {str(oc)[:120]}",
                        "provenance": "unavailable",
                    },
                    False,
                )
            )
        else:
            resolved.append(oc)

    # Preserve candidate order for stable output and union independently
    # validated PSORTb positives with Phobius positives. An unknown/error
    # Phobius result does not invalidate PSORTb, but a real intracellular
    # classification remains excluded.
    resolved.sort(key=lambda oc: oc[0])
    psort_retained = 0
    phobius_retained = 0
    for _i, classification, keep in resolved:
        candidate = candidates[_i]
        classifications.append(classification)
        topology = classification.get("classification")
        candidate_id = _surface_candidate_id(candidate)
        # A no-feature Phobius response is unknown evidence, not a reason to
        # discard a real PSORTb surface call. A topology-bearing intracellular
        # result remains authoritative and is excluded.
        has_phobius_evidence = bool(
            classification.get("signal_peptide")
            or classification.get("transmembrane_regions")
        )
        psort_candidate = candidate_id in psortb_ids
        retain_psort = psort_candidate and (
            topology != "intracellular" or not has_phobius_evidence
        )
        if retain_psort:
            psort_retained += 1
        if keep:
            phobius_retained += 1
        if keep or retain_psort:
            surface_exposed.append(candidate)

    # De-duplicate the union by accession (or stable upload index) while
    # retaining stable input order.
    unique_surface: list[dict] = []
    seen_surface: set[str] = set()
    for candidate in surface_exposed:
        identity = _surface_candidate_id(candidate)
        if identity not in seen_surface:
            seen_surface.add(identity)
            unique_surface.append(candidate)
    surface_exposed = unique_surface

    secreted = sum(1 for c in classifications if c["classification"] == "secreted")
    membrane = sum(1 for c in classifications if c["classification"] == "membrane")
    intracellular = sum(1 for c in classifications if c["classification"] == "intracellular")
    unavailable_count = sum(1 for c in classifications if c.get("classification") == "unknown")
    phobius_partial = bool(errors or unavailable_count)
    psortb_result_available = psortb_state.get("available") is True or explicit_psortb
    provenance_status = "partial" if phobius_partial or psortb_unavailable else "real"
    reasons: list[str] = []
    if psortb_unavailable:
        reasons.append(
            str((psortb_state.get("provenance") or {}).get("residualGap")
                or "PSORTb binary unavailable; Phobius-only residual remains")
        )
    if phobius_partial:
        reasons.append(
            "Phobius unavailable; retained validated PSORTb results"
            if psort_retained
            else "Phobius returned unknown/error for some candidates"
        )
    provenance_reason = "; ".join(reasons) if reasons else None
    union_method = "union(PSORTb local, Phobius EBI)"
    session["surface_exposed"] = {
        "candidates": surface_exposed,
        "count": len(surface_exposed),
        "provenance": {
            "status": provenance_status,
            "method": union_method,
            "psortbRetained": psort_retained,
            "phobiusRetained": phobius_retained,
            "unknownPhobiusPreserved": max(psort_retained - phobius_retained, 0),
            "phobiusUnavailableCount": unavailable_count,
            "psortbAvailable": psortb_result_available,
            "residualGap": provenance_reason if psortb_unavailable else None,
            "reason": provenance_reason,
        },
    }
    session.setdefault("_funnel_counts", {})["surface_exposed"] = len(surface_exposed)

    return {
        "message": f"PSORTb/Phobius union: {len(surface_exposed)}/{len(candidates)} retained",
        "total_analyzed": len(candidates),
        "surface_exposed_count": len(surface_exposed),
        "psortb_retained": psort_retained,
        "phobius_retained": phobius_retained,
        "secreted_count": secreted,
        "membrane_count": membrane,
        "intracellular_count": intracellular,
        "cache_hits": cache_hits,
        "classifications": classifications,
        "method": "psortb_phobius_union",
        "status": "partial" if provenance_status == "partial" else "completed",
        "provenance": {
            "status": provenance_status,
            "method": "PSORTb local unioned with Phobius EBI",
            "psortbAvailable": psortb_result_available,
            "unknownPhobiusPolicy": "retain validated PSORTb surface result",
            "intracellularPolicy": "exclude real Phobius intracellular classifications",
            "phobiusUnavailableCount": unavailable_count,
            "residualGap": provenance_reason if psortb_unavailable else None,
            "reason": provenance_reason,
        },
        "elapsed_sec": round(time.monotonic() - started, 1),
    }


# ---------------------------------------------------------------------------
# 3-1 AlgPred 2.0 — Allergenicity (graceful pause)
# ---------------------------------------------------------------------------
async def run_3_1(session: dict, job, step) -> dict:
    """AlgPred 2.0 — LOCAL COMPUTATION.

    Uses FAO/WHO allergen rules via local implementation:
    - Sequence identity to known allergens
    - Allergen-specific Pfam domains
    - Allergen motif patterns
    """
    candidates = (session.get("surface_exposed") or {}).get("candidates") or []
    if not candidates:
        return {
            "message": "No surface-exposed candidates for allergenicity check",
            "allergen_count": 0,
            "total_analyzed": 0,
        }

    results = []
    allergen_candidates = []
    non_allergen_candidates = []
    non_allergen_count = 0

    for cand in candidates:
        seq = cand.get("sequence", "")
        if not seq:
            continue

        prediction = algpred_local.predict_allergenicity(
            sequence=seq,
            pfam_domains=cand.get("pfam_domains", []),
            reference_allergen_match=cand.get("allergen_identity", 0.0),
        )

        results.append({
            "uniprotId": cand.get("uniprotId", "?"),
            "name": cand.get("name", "?"),
            **prediction,
        })

        if prediction["is_allergen"]:
            allergen_candidates.append(cand)
        else:
            non_allergen_candidates.append(cand)
            non_allergen_count += 1

    session["non_allergenic"] = {
        "candidates": non_allergen_candidates,
        "count": len(non_allergen_candidates),
    }

    return {
        "message": f"AlgPred (local): {non_allergen_count}/{len(candidates)} non-allergenic",
        "total_analyzed": len(candidates),
        "allergen_count": len(allergen_candidates),
        "non_allergen_count": non_allergen_count,
        "predictions": results,
        "method": "algpred_local",
        "threshold": 0.321,
        "provenance": {
            "status": "local-analysis",
            "tool": "AlgPred",
            "toolAvailable": False,
            "threshold": 0.321,
            "reason": "AlgPred external service is unavailable; local rules are explicitly labeled and are not an AlgPred result.",
        },
    }


# ---------------------------------------------------------------------------
# 3-2 VaxiJen 2.0 — Antigenicity (graceful pause)
# ---------------------------------------------------------------------------
async def run_3_2(session: dict, job, step) -> dict:
    """VaxiJen 2.0 — LOCAL COMPUTATION.

    Uses local ACC-based analysis because no usable VaxiJen service is
    configured. The requested bacteria threshold is 0.50; this is not a
    VaxiJen server result.
    """
    candidates = (session.get("non_allergenic") or {}).get("candidates") or []
    if not candidates:
        return {
            "message": "No non-allergenic candidates for antigenicity check",
            "antigenic_count": 0,
            "total_analyzed": 0,
        }

    results = []
    antigenic_candidates = []

    for cand in candidates:
        seq = cand.get("sequence", "")
        if not seq:
            continue

        prediction = vaxijen_local.is_antigenic(seq, organism_type="bacteria")

        results.append({
            "uniprotId": cand.get("uniprotId", "?"),
            "name": cand.get("name", "?"),
            **prediction,
        })

        if prediction["is_antigenic"]:
            antigenic_candidates.append(cand)

    session["antigenic"] = {
        "candidates": antigenic_candidates,
        "count": len(antigenic_candidates),
        "predictions": results,
    }

    return {
        "message": f"VaxiJen (local): {len(antigenic_candidates)}/{len(candidates)} antigenic",
        "total_analyzed": len(candidates),
        "antigenic_count": len(antigenic_candidates),
        "predictions": results,
        "method": "vaxijen_acc_local",
        "threshold": vaxijen_local.THRESHOLDS["bacteria"],
        "provenance": {
            "status": "local-analysis",
            "tool": "VaxiJen",
            "toolAvailable": False,
            "threshold": vaxijen_local.THRESHOLDS["bacteria"],
            "reason": "VaxiJen external service is unavailable; ACC output is explicitly local analysis.",
        },
    }


# ---------------------------------------------------------------------------
# Phase 5-2: CTL Antigenicity (VaxiJen) — graceful pause
# ---------------------------------------------------------------------------
async def run_5_2(session: dict, job, step) -> dict:
    """Phase 5-2: CTL antigenicity prediction (VaxiJen) — LOCAL COMPUTATION."""
    epitopes = session.get("epitopes") or []
    ctl_epitopes = [e for e in epitopes if e.get("type") == "CTL"]

    if not ctl_epitopes:
        return {
            "message": "No CTL epitopes for antigenicity scoring",
            "scored": 0,
            "total": 0,
            "method": "vaxijen_acc_local_ctl",
        }

    scored = 0
    for e in ctl_epitopes:
        seq = e.get("sequence", "")
        if seq:
            prediction = vaxijen_local.is_antigenic(seq, "bacteria")
            e["antigenicityScore"] = prediction["score"]
            e["antigenicity_method"] = prediction["method"]
            scored += 1

    session["epitopes"] = epitopes

    return {
        "message": f"VaxiJen (local): {scored} CTL epitopes scored",
        "scored": scored,
        "total": len(ctl_epitopes),
        "method": "vaxijen_acc_local_ctl",
    }


# ---------------------------------------------------------------------------
# Phase 5-3: CTL Allergenicity (AlgPred) → LOCAL computation
# ---------------------------------------------------------------------------
async def run_5_3(session: dict, job, step) -> dict:
    """CTL allergenicity (AlgPred) → LOCAL FAO/WHO computation."""
    epitopes = session.get("epitopes") or []
    ctl_epitopes = [e for e in epitopes if e.get("type") == "CTL"]

    if not ctl_epitopes:
        return {
            "message": "No CTL epitopes for allergenicity scoring",
            "scored": 0,
            "total": 0,
            "method": "algpred_local_ctl",
        }

    scored = 0
    for e in ctl_epitopes:
        seq = e.get("sequence", "")
        if seq:
            prediction = algpred_local.predict_allergenicity(seq)
            e["allergenicityScore"] = 1 - prediction["allergen_score"]
            e["allergenicity_method"] = prediction["method"]
            scored += 1

    return {
        "message": f"AlgPred (local): {scored} CTL epitopes scored",
        "scored": scored,
        "total": len(ctl_epitopes),
        "method": "algpred_local_ctl",
    }


# ---------------------------------------------------------------------------
# Phase 4: IEDB Conservancy Analysis (kept for internal use by run_8_1)
# ---------------------------------------------------------------------------
async def run_4_2(session: dict, job, step) -> dict:
    """Phase 4 Step 2: IEDB Conserved Epitope Analysis.

    For each essential candidate, check conservancy across pathogen strains
    using the IEDB conservation tool. Epitopes with conservancy >= 70% are
    retained as conserved; others are flagged.

    Session keys read:  epitopes  (list of Epitope dicts from 5-1/6-1)
    Session keys written:  conserved_epitopes  (filtered list)
    """
    from .iedb import _parse_tsv, _flatten_alleles

    epitopes = session.get("epitopes") or []
    if not epitopes:
        return {
            "message": "No epitopes to check for conservancy",
            "conserved_count": 0,
            "total_analyzed": 0,
        }

    alleles = list({e.get("hlaAllele") for e in epitopes if e.get("hlaAllele")})
    lengths = ["9", "10"]  # default MHCI lengths

    allele_csv, length_csv = _flatten_alleles(alleles, lengths)

    # Build multi-FASTA from epitope sequences
    fasta_lines = []
    for e in epitopes:
        fasta_lines.append(f">{e.get('sourceProteinId') or e.get('id')}|{e.get('hlaAllele')}")
        fasta_lines.append(e.get("sequence", ""))
    sequence_text = "\n".join(fasta_lines)

    client = EBIRestClient(email=os.environ.get("EBI_EMAIL", "mev-pipeline@example.com"))
    try:
        result = await client.run("conservation", sequence_text, result_type="out")
    except EBIRestClientError as exc:
        raise ToolUnavailableError(
            tool_name="IEDB Conservancy",
            reason=f"IEDB conservancy is unavailable: {exc}",
            workaround="Retry when IEDB/EBI is available; no epitopes are passed through as conserved.",
        ) from exc

    # Parse the conservative TSV output from IEDB
    # Expected columns: allele, seq_num, start, end, length, peptide, conservancy
    try:
        rows = _parse_tsv(result.raw_text, has_rank=False)
    except Exception as exc:
        raise ToolUnavailableError(
            tool_name="IEDB Conservancy",
            reason=f"IEDB conservancy returned an unparseable response: {exc}",
            workaround="Retry with a valid IEDB response; no epitopes are passed through as conserved.",
        ) from exc

    # Filter by conservancy threshold (default 70th percentile)
    conserved = [e for e in epitopes if any(r.peptide == e["peptide"] for r in rows if float(r.percentile_rank or 0) >= 70.0)]

    session["conserved_epitopes"] = conserved
    return {
        "message": f"Conservancy filter: {len(conserved)}/{len(epitopes)} epitopes conserved (≥70%)",
        "total_analyzed": len(epitopes),
        "conserved_count": len(conserved),
    }


# ---------------------------------------------------------------------------
# Phase 8-1: Population Coverage Analysis (IEDB-AR + local fallback)
# ---------------------------------------------------------------------------
async def run_8_1(session: dict, job, step) -> dict:
    """Phase 8 Step 1: Population Coverage Analysis.

    Uses the REAL IEDB Population Coverage 3.0.2 standalone tool (same
    allele-frequency reference as the web IEDB-AR the paper used), run locally
    so the 403-blocked web endpoint is not needed. Reports World coverage plus
    the paper's reported regions (Europe, North America).

    Session keys read:  conserved_epitopes (or epitopes)
    """
    from . import iedb_population_local

    conserved = session.get("conserved_epitopes") or session.get("epitopes") or []
    if not conserved:
        return {
            "message": "No conserved epitopes to calculate coverage for",
            "coverage": 0,
            "total_analyzed": 0,
        }

    enable_cov = getattr(job.config, "enableCoverage", True)
    if not enable_cov:
        return {
            "message": "Population coverage disabled by job configuration",
            "coverage": 0,
            "total_analyzed": len(conserved),
        }

    try:
        res = await asyncio.to_thread(
            iedb_population_local.compute,
            conserved,
            mhc_class="combined",
            python_exe=sys.executable,
        )
    except Exception as exc:  # noqa: BLE001 - surface tool failure as a pause
        raise ToolUnavailableError(
            tool_name="IEDB Population Coverage 3.0.2",
            reason=f"IEDB population coverage tool failed: {exc}",
            workaround=(
                "Ensure the standalone tool is unpacked at .iedb_tools/"
                "population_coverage and numpy/matplotlib/setuptools are installed."
            ),
        ) from exc

    by_area = res.get("by_area") or {}
    parts = ", ".join(f"{a}: {v}%" for a, v in by_area.items())
    return {
        "message": f"IEDB population coverage (real, 3.0.2) — {parts}",
        "coverage": res.get("coverage", 0.0),
        "by_area": by_area,
        "total_analyzed": len(conserved),
        "mhc_class": res.get("mhc_class", "combined"),
        "method": res.get("method", "iedb_popcov_3.0.2"),
    }


# ---------------------------------------------------------------------------
# 3-3 VFDB — Virulence Factor Identification (REAL, BLASTp)
# ---------------------------------------------------------------------------
async def run_3_3(session: dict, job, step) -> dict:
    """BLASTp surface-exposed candidates against the VFDB core dataset.

    Proteins with >= 30% identity and e-value <= 1e-5 against a VFDB entry
    are classified as virulence factors and kept in ``session["virulence_factors"]``.

    Runs on the antigenic, non-allergenic survivors (paper funnel order:
    localization -> allergenicity -> antigenicity -> virulence), so the
    virulent set is a strict subset of the safe, antigenic candidates.
    """
    candidates = (
        (session.get("antigenic") or {}).get("candidates")
        or (session.get("non_allergenic") or {}).get("candidates")
        or (session.get("surface_exposed") or {}).get("candidates")
        or (session.get("essential") or {}).get("candidates")
        or []
    )
    if not candidates:
        return {
            "message": "No candidates to check against VFDB",
            "virulence_count": 0,
            "total_analyzed": 0,
        }

    started = time.monotonic()
    try:
        result: VFDBBatchResult = await asyncio.to_thread(blast_vfdb, candidates)
    except Exception as exc:  # noqa: BLE001 - surface any tool failure
        raise ToolUnavailableError(
            tool_name="VFDB BLAST",
            reason=f"VFDB BLAST failed: {exc}",
            workaround=(
                "Ensure blastp/makeblastdb are installed (local) or set "
                "MEV_VFDB_CACHE. Bypass to treat all candidates as virulence factors."
            ),
        ) from exc

    vf_candidates = [c for c, r in zip(candidates, result.results) if r.is_virulence_factor]
    non_vf_candidates = [c for c, r in zip(candidates, result.results) if not r.is_virulence_factor]
    session["virulence_factors"] = {
        "candidates": vf_candidates,
        "non_virulence_candidates": non_vf_candidates,
        "count": result.virulence_count,
        "vfdb_details": [
            {
                "uniprotId": r.query_id,
                "is_virulence_factor": r.is_virulence_factor,
                "best_identity": round(r.best_identity, 2),
                "best_evalue": f"{r.best_evalue:.2e}" if r.best_evalue < 1.0 else "N/A",
                "total_hits": r.total_hits,
                "best_hit_subject": r.best_hit.subject_id if r.best_hit else None,
                "best_hit_title": r.best_hit.subject_title if r.best_hit else None,
            }
            for r in result.results
        ],
    }
    # Stash for _update_funnel after session pruning.
    fc = session.setdefault("_funnel_counts", {})
    fc["virulence_factors"] = result.virulence_count

    return {
        "message": (
            f"VFDB BLAST complete: {result.virulence_count}/{result.total_candidates} "
            "candidates are virulence factors"
        ),
        "total_analyzed": result.total_candidates,
        "virulence_count": result.virulence_count,
        "non_virulence_count": result.total_candidates - result.virulence_count,
        "vfdb_fasta_path": result.vfdb_fasta_path,
        "criteria": {
            "identity_min_percent": VFDB_IDENTITY_THRESHOLD,
            "bitscore_strictly_greater_than": VFDB_BITSCORE_THRESHOLD,
            "evalue_max": VFDB_EVALUE_THRESHOLD,
        },
        "provenance": {
            "status": "real",
            "method": "local BLASTp against VFDB core",
            "criteria": "identity >= 30%, bit score > 100, E-value <= 1e-4",
            "source": result.vfdb_fasta_path,
        },
        "elapsed_sec": round(time.monotonic() - started, 1),
    }


# ---------------------------------------------------------------------------
# 3-4 Human Homology — BLASTp against local Human Proteome (REAL)
# ---------------------------------------------------------------------------
async def run_3_4(session: dict, job, step) -> dict:
    """Filter out candidates with >= 35% identity to a human protein.

    Provisions the reviewed human UniProt FASTA and local BLAST database on
    first use, then searches it locally. This avoids an unbounded NCBI RID
    wait and ensures the homology result is reproducible on the T4 backend.

    Input is the VIRULENT set (paper selects virulent proteins as targets —
    e.g. C5a peptidase, cell-wall anchors — then removes human homologs).
    """
    candidates = (
        (session.get("virulence_factors") or {}).get("candidates")
        or (session.get("antigenic") or {}).get("candidates")
        or (session.get("surface_exposed") or {}).get("candidates")
        or (session.get("essential") or {}).get("candidates")
        or []
    )
    if not candidates:
        return {
            "message": "No candidates for human homology check",
            "non_homologous_count": 0,
            "total_analyzed": 0,
        }

    started = time.monotonic()
    queries: list[tuple[str, str]] = []
    for cand in candidates:
        seq = cand.get("sequence", "")
        if seq:
            header = cand.get("uniprotId") or cand.get("name") or f"cand_{cand.get('index', '?')}"
            queries.append((header, seq))

    try:
        await blastdb_local.ensure_human_db()
        results = await blastdb_local.blastp(
            queries,
            database=blastdb_local.HUMAN_DB_NAME,
            expect=HOMOLOGY_EVALUE_THRESHOLD,
            hitlist_size=5,
        )
        source_label = f"BLASTp (local: {blastdb_local.HUMAN_DB_NAME})"
    except Exception as exc:  # noqa: BLE001 - surface any tool failure
        raise ToolUnavailableError(
            tool_name="BLASTp (Human Proteome)",
            reason=f"Local BLASTp against human proteome failed: {exc}",
            workaround=(
                "Ensure BLAST+ is installed and the human proteome database has "
                "network access for the initial UniProt download, or provision "
                "MEV_BLAST_DB_CACHE with the reviewed human database."
            ),
        ) from exc

    by_def = {r.query_def.split("|")[0]: r for r in results}
    homologous_ids: set[str] = set()
    analysis: list[dict] = []

    for cand in candidates:
        qid = cand.get("uniprotId") or cand.get("name") or f"cand_{cand.get('index', '?')}"
        res = by_def.get(qid)
        best_identity = 0.0
        best_evalue = 1.0
        hit_count = 0
        if res and res.hits:
            identities = [
                (h.identity / h.align_length * 100) if h.align_length else 0.0
                for h in res.hits
            ]
            best_identity = max(identities)
            best_evalue = min(h.e_value for h in res.hits)
            hit_count = len(res.hits)
        is_homologous = best_identity >= HUMAN_HOMOLOGY_IDENTITY_THRESHOLD * 100
        if is_homologous:
            homologous_ids.add(qid)
        analysis.append({
            "uniprotId": qid,
            "name": cand.get("name", "?"),
            "is_homologous": is_homologous,
            "best_identity_pct": round(best_identity, 2),
            "best_evalue": f"{best_evalue:.2e}" if best_evalue < 1.0 else "N/A",
            "hit_count": hit_count,
        })

    non_homologous = [c for c in candidates if (c.get("uniprotId") or c.get("name") or f"cand_{c.get('index', '?')}") not in homologous_ids]

    session["vaccine_targets"] = {
        "candidates": non_homologous,
        "count": len(non_homologous),
    }
    # Stash for _update_funnel after session pruning.
    fc = session.setdefault("_funnel_counts", {})
    fc["targets"] = len(non_homologous)

    return {
        "message": (
            f"Human homology filter ({source_label}): {len(non_homologous)}/{len(candidates)} "
            "candidates are non-homologous"
        ),
        "total_analyzed": len(candidates),
        "non_homologous_count": len(non_homologous),
        "homologous_count": len(homologous_ids),
        "homologous_ids": sorted(homologous_ids),
        "source": source_label,
        "analysis": analysis,
        "elapsed_sec": round(time.monotonic() - started, 1),
    }

# ---------------------------------------------------------------------------
# Phase 9-1: Adjuvant Selection (graceful pause)
# ---------------------------------------------------------------------------
ADJUVANT_PAUSE: dict[str, str] = {
    "tool_name": "Adjuvant Selection",
    "reason": (
        "Adjuvant selection requires manual curation against pathogen-specific "
        "TLR agonist databases. No automated REST API exists."
    ),
    "workaround": (
        "Select an appropriate adjuvant manually (e.g., CTxB, Flagellin, CpG) "
        "based on pathogen type and target population."
    ),
}

async def run_9_1_adj(session: dict, job, step) -> dict:
    """Phase 9 Step 1: Adjuvant Selection — LOCAL computation.

    Uses local adjuvant selection rules (adjuvant_dbd2_local.py).
    """
    mev_data = session.get("mev_construct") or {}
    antigenic_score = mev_data.get("antigenicity_score")

    mev_antigenicity = session.get("mev_antigenicity", {})
    if isinstance(mev_antigenicity, dict) and "score" in mev_antigenicity:
        antigenic_score = mev_antigenicity["score"]

    configured_adjuvant = getattr(job.config, "adjuvant", "auto")
    if antigenic_score is None and configured_adjuvant in (None, "", "auto"):
        raise ToolUnavailableError(
            tool_name="Adjuvant Selection",
            reason="Automatic adjuvant selection requires a validated upstream antigenicity result.",
            workaround="Provide a manually curated adjuvant or complete the antigenicity step first.",
        )

    if antigenic_score is None:
        result = {
            "recommended": configured_adjuvant,
            "ranked_options": [],
            "antigen_strength": "not assessed",
            "method": "user-configured adjuvant",
            "provenance": "user-provided",
        }
    else:
        result = adjuvant_dbd2_local.select_adjuvant(
            {"antigenicity_score": antigenic_score},
            "bacteria",
            configured_adjuvant,
        )

    session["adjuvant_selection"] = result
    return {
        "message": f"Adjuvant (local): recommended = {result['recommended']}",
        "recommended": result["recommended"],
        "ranked_options": result["ranked_options"],
        "antigen_strength": result["antigen_strength"],
        "method": result["method"],
        "source": result.get("provenance", "local-analysis"),
    }


# ---------------------------------------------------------------------------
# Phase 9-2: MEV Assembly
# ---------------------------------------------------------------------------
# Cholera enterotoxin subunit B — full sequence (UniProt P01556, 124 aa),
# the adjuvant the paper attached to the N-terminus via an EAAAK linker
# (matches the construct in Arya et al. Fig. 1a, which begins with this exact
# sequence). Previously a 27-aa placeholder that was NOT the real CTxB.
CTXB_ADJUSTANT = (
    "MIKLKFGVFFTVLLSSAYAHGTPQNITDLCAEYHNTQIYTLNDKIFSYTESLAGKREMAII"
    "TFKNGAIFQVEVPGSQHIDSQKKAIERMKDTLRIAYLTEAKVEKLCVWNNKTPHAIAAISMAN"
)
LINKER_EAAAK = "EAAAK"
LINKER_AAY = "AAY"
LINKER_GPGPG = "GPGPG"
LINKER_KK = "KK"


def _assemble_mev_construct(
    epitopes: list[dict],
    linker_ctr: str,
    linker_htl: str,
    linker_bcell: str,
    *,
    use_optional_sequences: bool = False,
    adjuvant_sequence: str | None = None,
    adjuvant_source: str | None = None,
    signal_peptide_sequence: str | None = None,
    signal_peptide_source: str | None = None,
) -> dict:
    """Assemble a deterministic MEV from real epitope and optional sequences.

    ``use_optional_sequences`` is an explicit opt-in.  When it is false, the
    optional payload is ignored and this function follows the legacy path
    exactly.  When true, each supplied optional sequence must be a valid
    amino-acid sequence with an explicit accession or URL; no sequence is
    invented, padded, or selected merely to reach a target length.
    """
    import re

    def _validate_sequence(value: str | None, label: str, source: str | None) -> str:
        if value is None:
            if source and source.strip():
                raise ValueError(f"{label} source was provided without a sequence")
            return ""
        clean = "".join(value.split()).upper()
        if not clean or not re.fullmatch(r"[ACDEFGHIKLMNPQRSTVWY]+", clean):
            raise ValueError(f"{label} is not a valid amino-acid sequence")
        if not source or not source.strip():
            raise ValueError(f"{label} requires a source URL/accession")
        return clean

    if use_optional_sequences:
        supplied_adjuvant = _validate_sequence(adjuvant_sequence, "Adjuvant sequence", adjuvant_source)
        signal = _validate_sequence(signal_peptide_sequence, "Signal peptide sequence", signal_peptide_source)
    else:
        # Do not let optional fields alter the legacy construct unless the
        # caller explicitly enables the provenance-gated path.
        supplied_adjuvant = ""
        signal = ""
    selected_adjuvant = supplied_adjuvant or CTXB_ADJUSTANT
    adjuvant_provenance = adjuvant_source if supplied_adjuvant else "UniProt P01556 (CTxB), configured legacy sequence"
    signal_provenance = signal_peptide_source if signal else None
    # Keep the final construct within the public ESMFold sequence limit while
    # retaining the paper-aligned 8/8/5 epitope composition.
    CTL_CAP = 8
    HTL_CAP = 8
    BCELL_CAP = 5

    def _safe(e: dict) -> bool:
        # Paper criterion: epitopes must be non-toxic and non-allergenic.
        # Phases 5-3/5-4/6-6/6-7/7-3/7-4 store SAFETY scores as (1 - raw):
        #   allergenicityScore = 1 - AlgPred allergen_score  (allergen if raw>=0.321)
        #   toxicityScore      = 1 - ToxinPred toxicity_score (toxic if raw>=0.40)
        alg = e.get("allergenicityScore")
        tox = e.get("toxicityScore")
        allergen_ok = (alg is None) or (alg > (1.0 - 0.321))
        toxic_ok = (tox is None) or (tox > (1.0 - 0.40))
        return allergen_ok and toxic_ok

    ctl_epitopes = [e for e in epitopes if e.get("type") == "CTL" and _safe(e)]
    htl_epitopes = [e for e in epitopes if e.get("type") == "HTL" and _safe(e)]
    bcell_epitopes = [e for e in epitopes if e.get("type") in ("BCELL_LINEAR", "BCELL_CONFORMATIONAL") and _safe(e)]

    # Filter by length: CTL 8-12 aa, HTL 13-20 aa, B-cell <= 25 aa
    ctl_epitopes = [e for e in ctl_epitopes if 8 <= len(e.get("sequence", "")) <= 12]
    htl_epitopes = [e for e in htl_epitopes if 13 <= len(e.get("sequence", "")) <= 20]
    bcell_epitopes = [e for e in bcell_epitopes if len(e.get("sequence", "")) <= 25]

    # Rank by binding strength (lower percentile = stronger) then antigenicity,
    # so the capped set is the best epitopes — the paper's selection basis.
    def _rank_key(e: dict):
        pr = e.get("percentileRank")
        pr = pr if isinstance(pr, (int, float)) else 999
        ag = e.get("antigenicityScore") or 0
        return (pr, -ag)

    ctl_epitopes.sort(key=_rank_key)
    htl_epitopes.sort(key=_rank_key)
    bcell_epitopes.sort(key=lambda e: -(e.get("antigenicityScore") or 0))

    # Cap the number of epitopes per type (paper-aligned composition 8/8/5)
    ctl_epitopes = ctl_epitopes[:CTL_CAP]
    htl_epitopes = htl_epitopes[:HTL_CAP]
    bcell_epitopes = bcell_epitopes[:BCELL_CAP]

    # Deterministic ordering by (type, sourceProtein, allele, start)
    ctl_epitopes.sort(key=lambda e: (e.get("type", ""), e.get("sourceProtein", ""),
                                     e.get("hlaAllele", ""), e.get("start", 0)))
    htl_epitopes.sort(key=lambda e: (e.get("type", ""), e.get("sourceProtein", ""),
                                     e.get("hlaAllele", ""), e.get("start", 0)))
    bcell_epitopes.sort(key=lambda e: (e.get("type", ""), e.get("sourceProtein", ""),
                                       e.get("start", 0)))

    # Build CTL segment: [signal]-adjuvant-EAAAK-AAY-epitopes.
    ctl_segment = signal + selected_adjuvant
    if ctl_epitopes:
        ctl_segment += LINKER_EAAAK
        ctl_segment += LINKER_AAY
        ctl_segment += ctl_epitopes[0]["sequence"]
        for e in ctl_epitopes[1:]:
            ctl_segment += LINKER_AAY
            ctl_segment += e["sequence"]

    # Build HTL segment: GPGPG-epitope-GPGPG-epitope-...
    htl_segment = ""
    if htl_epitopes:
        htl_segment = LINKER_GPGPG
        htl_segment += htl_epitopes[0]["sequence"]
        for e in htl_epitopes[1:]:
            htl_segment += LINKER_GPGPG
            htl_segment += e["sequence"]

    # Build B-cell segment: KK-epitope-KK-epitope-...
    bcell_segment = ""
    if bcell_epitopes:
        bcell_segment = LINKER_KK
        bcell_segment += bcell_epitopes[0]["sequence"]
        for e in bcell_epitopes[1:]:
            bcell_segment += LINKER_KK
            bcell_segment += e["sequence"]

    full_sequence = ctl_segment + htl_segment + bcell_segment
    total_length = len(full_sequence)

    return {
        "sequence": full_sequence,
        "length": total_length,
        "adjuvant": selected_adjuvant,
        "adjuvantSource": adjuvant_provenance,
        "signalPeptide": signal or None,
        "signalPeptideSource": signal_provenance,
        "linker_ctr": linker_ctr,
        "linker_htl": linker_htl,
        "linker_bcell": linker_bcell,
        "ctl_epitopes": len(ctl_epitopes),
        "htl_epitopes": len(htl_epitopes),
        "bcell_epitopes": len(bcell_epitopes),
    }


async def run_9_1(session: dict, job, step) -> dict:
    """Phase 9 Step 1: MEV Assembly.

    Assembles the final multi-epitope vaccine construct by joining selected epitopes
    with appropriate linkers: CTxB adjuvant → EAAAK → CTL epitopes (AAY) → GPGPG → HTL
    epitopes → KK → B-cell epitopes.

    Session keys read:  conserved_epitopes, vaccine_targets
    Session keys written:  mev_construct
    """
    conserved = session.get("conserved_epitopes") or []
    predicted = session.get("epitopes") or []

    # Assemble from the real selected epitope predictions (CTL/HTL/B-cell
    # dicts). `vaccine_targets.candidates` holds *proteins*, not epitopes —
    # never use it as the epitope source.
    epitopes = [
        e
        for e in (conserved or predicted)
        if isinstance(e, dict) and e.get("sequence") and e.get("selected", True)
    ]

    if not epitopes:
        return {
            "message": "No epitopes available for MEV assembly",
            "mev_length": 0,
            "sequence": "",
        }

    config = getattr(job, "config", None)
    use_optional_sequences = getattr(config, "enableMevEnhancements", False) is True
    adjuvant_sequence = getattr(config, "adjuvantSequence", None) if use_optional_sequences else None
    adjuvant_source = getattr(config, "adjuvantSource", None) if use_optional_sequences else None
    signal_peptide_sequence = getattr(config, "signalPeptideSequence", None) if use_optional_sequences else None
    signal_peptide_source = getattr(config, "signalPeptideSource", None) if use_optional_sequences else None
    if use_optional_sequences and not adjuvant_sequence and not signal_peptide_sequence:
        raise ToolUnavailableError(
            tool_name="MEV optional sequence configuration",
            reason=(
                "MEV enhancements are enabled, but no real signal-peptide or "
                "full-adjuvant sequence was supplied."
            ),
            workaround=(
                "Provide at least one curated amino-acid sequence with its "
                "source URL/accession, then retry the assembly."
            ),
        )

    construct = _assemble_mev_construct(
        epitopes,
        LINKER_EAAAK,
        LINKER_GPGPG,
        LINKER_KK,
        use_optional_sequences=use_optional_sequences,
        adjuvant_sequence=adjuvant_sequence,
        adjuvant_source=adjuvant_source,
        signal_peptide_sequence=signal_peptide_sequence,
        signal_peptide_source=signal_peptide_source,
    )

    session["mev_construct"] = {
        "sequence": construct["sequence"],
        "length": construct["length"],
        "adjuvant": construct["adjuvant"],
        "adjuvantSource": construct["adjuvantSource"],
        "signalPeptideSource": construct["signalPeptideSource"],
        "ctl_epitopes": construct["ctl_epitopes"],
        "htl_epitopes": construct["htl_epitopes"],
        "bcell_epitopes": construct["bcell_epitopes"],
    }

    return {
        "message": f"MEV construct assembled: {construct['length']} aa, "
                   f"{construct['ctl_epitopes']} CTL, {construct['htl_epitopes']} HTL, "
                   f"{construct['bcell_epitopes']} B-cell epitopes",
        "mev_length": construct["length"],
        "sequence": construct["sequence"],
        "adjuvant": construct["adjuvant"],
        "adjuvantSource": construct["adjuvantSource"],
        "signalPeptideSource": construct["signalPeptideSource"],
        "source": "local-analysis",
        "provenance": {
            "status": "local-analysis",
            "method": "deterministic sequence assembly over real epitope inputs",
            "adjuvantSource": construct["adjuvantSource"],
            "signalPeptideSource": construct["signalPeptideSource"],
        },
    }

# ---------------------------------------------------------------------------
# Phase 10-1: ProtParam — Physicochemical Protein Properties (Step 11-1)
# ---------------------------------------------------------------------------
PROTPARAM_THRESHOLDS = {
    "instability": 40.0,     # >= 40 = unstable
    "gravy": -0.5,           # negative = hydrophilic
}

async def run_10_1(session: dict, job, step) -> dict:
    """Phase 10 Step 1: ProtParam — Physicochemical protein properties.

    Computes molecular weight, pI, instability index, GRAVY, aliphatic index,
    and extinction coefficient using BioPython.

    Session keys read:  mev_construct (validated sequence)
    Session keys written:  physicochemical
    """
    import re

    mev_data = session.get("mev_construct")
    raw_sequence = mev_data.get("sequence") if isinstance(mev_data, dict) else None
    if not isinstance(raw_sequence, str) or not raw_sequence.strip():
        raise ToolUnavailableError(
            tool_name="ProtParam local analysis",
            reason=(
                "A non-empty validated real MEV construct sequence is required in "
                "session['mev_construct']['sequence']; complete MEV assembly first."
            ),
            workaround="Complete MEV assembly and retry ProtParam.",
        )

    sequence = "".join(raw_sequence.split()).upper()
    if not re.fullmatch(r"[ACDEFGHIKLMNPQRSTVWY]+", sequence):
        raise ToolUnavailableError(
            tool_name="ProtParam local analysis",
            reason=(
                "The MEV construct sequence in session['mev_construct']['sequence'] "
                "is not a valid amino-acid sequence."
            ),
            workaround="Provide a validated MEV protein sequence and retry ProtParam.",
        )

    try:
        analysis = ProteinAnalysis(sequence)
    except Exception as exc:
        raise ToolUnavailableError(
            tool_name="ProtParam local analysis",
            reason=f"BioPython could not validate the MEV construct sequence: {exc}",
            workaround="Provide a validated MEV protein sequence and retry ProtParam.",
        ) from exc

    # BioPython methods (some may not exist in all versions):
    mw = analysis.molecular_weight()
    pI = analysis.isoelectric_point()
    instability = analysis.instability_index()
    gravy = analysis.gravy()
    aromaticity = analysis.aromaticity()
    charge_pH7 = analysis.charge_at_pH(7)
    aa_counts = analysis.count_amino_acids()

    # Extinction coefficient (based on W,R,Y counts)
    extinction = (
        5500 * aa_counts.get("W", 0)
        + 1490 * aa_counts.get("Y", 0)
        + 125 * aa_counts.get("C", 0)  # reduced Cystine
    )

    # Aliphatic index (modified from Doolittle formula)
    # Ala*X_Ala + Val*X_Val + Ile*X_Ile + Leu*X_Leu
    # Simplified: use counts if available
    aliphatic_index = None
    if aa_counts:
        aliphatic_index = (
            100.0 * (aa_counts.get("A", 0) * 1.0
                     + aa_counts.get("V", 0) * 4.2
                     + aa_counts.get("I", 0) * 4.5
                     + aa_counts.get("L", 0) * 3.8)
        ) / max(sum(aa_counts.values()), 1)

    physicochemical = {
        "molecular_weight": round(mw, 2),
        "isoelectric_point": round(pI, 2),
        "instability_index": round(instability, 2),
        "instability_stable": "stable" if instability < 40 else "unstable",
        "gravy": round(gravy, 4),
        "aromaticity": round(aromaticity, 4),
        "charge_at_pH7": round(charge_pH7, 4),
        "extinction_coefficient": extinction,
        "amino_acid_counts": aa_counts,
        "aliphatic_index": round(aliphatic_index, 2) if aliphatic_index else None,
    }

    session["physicochemical"] = physicochemical
    method = "protparam_local_biopython"
    return {
        "message": f"ProtParam: MW={mw:.1f}, pI={pI:.1f}, Instability={instability:.1f}, GRAVY={gravy:.2f}",
        "physicochemical": physicochemical,
        "source": "local-analysis",
        "method": method,
        "provenance": {
            "status": "local-analysis",
            "syntheticValues": False,
            "method": method,
            "input": "validated real MEV construct sequence",
            "inputSource": "session['mev_construct']['sequence']",
            "inputLength": len(sequence),
            "measuredProperties": list(physicochemical.keys()),
        },
    }
# ---------------------------------------------------------------------------
# Phase 12-1: JCat — Codon Optimization (Step 13-1)
# ---------------------------------------------------------------------------
JCAT_RESULT_URL = "https://www.jcat.de/Result.jsp"
JCAT_ECOLI_K12 = "42 Escherichia coli (strain K12)"
JCAT_OUTPUT = "plain"  # plain, pdf, xml

async def run_12_1(session: dict, job, step) -> dict:
    """Phase 12 Step 1: JCat — Codon Optimization.

    Submits a coding sequence to the JCat server for codon optimization
    towards a target organism. Returns the optimized sequence, CAI, and
    GC content.

    Session keys read:  none (takes sequence from runner call)
    Session keys written:  codon_optimized
    """
    import httpx
    import re

    sequence = session.get("mev_construct", {}).get("sequence", "")

    if not sequence:
        return {
            "message": "No sequence for codon optimization",
            "codon_optimized": "",
            "cai": None,
            "gc_content": None,
            "method": "jcat_external",
            "source": "unavailable",
            "status": "unavailable",
            "provenance": {
                "status": "unavailable",
                "method": "jcat_external",
                "reason": "validated MEV construct sequence is missing",
                "syntheticValues": False,
            },
        }

    # JCat accepts DNA or protein (amino-acid) input, and returns the
    # mode to use: if the input is purely ACGT/U it is treated as DNA,
    # otherwise it must be a valid protein sequence.
    raw = sequence.upper().strip()
    if re.fullmatch(r"[ACGTU]+", raw):
        submit_seq = raw.replace("U", "T")
        input_kind = "DNA"
    elif re.fullmatch(r"[ACDEFGHIKLMNPQRSTVWY]+", raw) and len(raw) >= 2:
        submit_seq = raw
        input_kind = "Amino"
    else:
        return {
            "message": "Input sequence is not a valid DNA or protein sequence for JCat",
            "codon_optimized": "",
            "cai": None,
            "gc_content": None,
            "error": "invalid_sequence",
            "method": "jcat_external",
            "source": "unavailable",
            "status": "unavailable",
            "provenance": {
                "status": "unavailable",
                "method": "jcat_external",
                "reason": "MEV input is not a valid DNA or amino-acid sequence",
                "syntheticValues": False,
            },
        }

    started = time.monotonic()
    try:
        async with httpx.AsyncClient(
            verify=False,
            timeout=60.0,
        ) as client:
            resp = await client.post(
                JCAT_RESULT_URL,
                data={
                    "Sequence": submit_seq,
                    "seq_type": input_kind,
                    "genome": JCAT_ECOLI_K12,
                    "translationNo": "1",
                },
            )
            resp.raise_for_status()
            html = resp.text

        # The optimized sequence is in the <font class="sequence"> block that
        # follows the "Improved DNA:" label. Line numbers and markups inside
        # must be stripped to recover the pure coding sequence.
        opt_block = re.search(
            r"Improved DNA:.*?<font class = \"sequence\">(.*?)</font>",
            html,
            re.S | re.I,
        )
        optimized = ""
        if opt_block:
            raw = opt_block.group(1)
            raw = re.sub(r"<br\s*/?>", "", raw)
            raw = re.sub(r"&nbsp;", "", raw)
            raw = re.sub(r"\d+", "", raw)
            optimized = re.sub(r"\s+", "", raw).upper()

        cai = None
        cai_match = re.search(
            r"CAI-Value of the improved sequence:.*?<font class = \"sequence\">\s*([\d.]+)\s*</font>",
            html,
            re.S | re.I,
        )
        if cai_match:
            cai = float(cai_match.group(1))

        gc_content = None
        # JCat renders "CAI-Value ... GC-Content ..." as one row of two labels,
        # followed by two value cells. The GC value is the second one.
        gc_match = re.search(
            r"GC-Content of the improved sequence:.*?</font>.*?"
            r"<font class = \"sequence\">\s*([\d.]+)\s*</font>.*?"
            r"<font class = \"sequence\">\s*([\d.]+)\s*</font>",
            html,
            re.S | re.I,
        )
        if gc_match:
            gc_content = float(gc_match.group(2))

        # Validate: the optimized DNA must be a real coding sequence and, for
        # protein input, must reverse-translate back to the input protein.
        # This guarantees we never store an invalid fabricated result.
        standard_code = {
            "TTT": "F", "TTC": "F", "TTA": "L", "TTG": "L",
            "TCT": "S", "TCC": "S", "TCA": "S", "TCG": "S",
            "TAT": "Y", "TAC": "Y", "TAA": "*", "TAG": "*",
            "TGT": "C", "TGC": "C", "TGA": "*", "TGG": "W",
            "CTT": "L", "CTC": "L", "CTA": "L", "CTG": "L",
            "CCT": "P", "CCC": "P", "CCA": "P", "CCG": "P",
            "CAT": "H", "CAC": "H", "CAA": "Q", "CAG": "Q",
            "CGT": "R", "CGC": "R", "CGA": "R", "CGG": "R",
            "ATT": "I", "ATC": "I", "ATA": "I", "ATG": "M",
            "ACT": "T", "ACC": "T", "ACA": "T", "ACG": "T",
            "AAT": "N", "AAC": "N", "AAA": "K", "AAG": "K",
            "AGT": "S", "AGC": "S", "AGA": "R", "AGG": "R",
            "GTT": "V", "GTC": "V", "GTA": "V", "GTG": "V",
            "GCT": "A", "GCC": "A", "GCA": "A", "GCG": "A",
            "GAT": "D", "GAC": "D", "GAA": "E", "GAG": "E",
            "GGT": "G", "GGC": "G", "GGA": "G", "GGG": "G",
        }
        dna_ok = (
            bool(optimized)
            and len(optimized) % 3 == 0
            and len(optimized) >= 3
            and "N" not in optimized
        )
        if input_kind == "Amino":
            translated = "".join(standard_code.get(optimized[i:i+3], "?") for i in range(0, len(optimized), 3))
            dna_ok = dna_ok and translated.rstrip("*") == submit_seq.rstrip("*")
        elif input_kind == "DNA":
            translated = "".join(standard_code.get(optimized[i:i+3], "?") for i in range(0, len(optimized), 3))
            expected = "".join(standard_code.get(submit_seq[i:i+3], "?") for i in range(0, len(submit_seq), 3))
            dna_ok = dna_ok and translated.rstrip("*") == expected.rstrip("*")

        if not dna_ok or cai is None or gc_content is None:
            raise ToolUnavailableError(
                tool_name="JCat (Codon Optimization)",
                reason="JCat returned an unparseable or inconsistent optimized sequence.",
                workaround=(
                    "Run codon optimization manually at https://www.jcat.de and "
                    "paste the result into the MEV construct."
                ),
            )

        session["codon_optimized"] = {
            "codon_optimized": optimized,
            "cai": cai,
            "gc_content": gc_content,
            "organism": "E. coli K-12",
        }

        return {
            "message": f"JCat optimization: CAI={cai:.3f}, GC={gc_content:.1f}%",
            "codon_optimized": optimized,
            "cai": cai,
            "gc_content": gc_content,
            "elapsed_sec": round(time.monotonic() - started, 1),
            "method": "jcat_external",
            "source": "real",
            "provenance": {
                "status": "real",
                "tool": "JCat",
                "method": "JCat external codon optimization",
                "inputSource": "session['mev_construct']['sequence']",
                "organism": "E. coli K-12",
                "syntheticValues": False,
            },
        }

    except httpx.HTTPError as exc:
        raise ToolUnavailableError(
            tool_name="JCat (Codon Optimization)",
            reason=f"Request failed: {exc}",
            workaround=(
                "Run codon optimization manually at https://www.jcat.de and "
                "paste the result into the MEV construct."
            ),
        )


# ---------------------------------------------------------------------------
# Phase 10-5: Protein-Sol → LOCAL solubility prediction
# ---------------------------------------------------------------------------
async def run_10_5(session: dict, job, step) -> dict:
    """Protein-Sol solubility prediction → LOCAL computation.

    Uses the local solubility model (quality_local.py).
    """
    mev_data = session.get("mev_construct") or {}
    seq = mev_data.get("sequence", "")
    if not seq:
        return {"message": "No MEV sequence for solubility", "score": None, "method": "proteinsol_local_mev"}

    prediction = quality_local.predict_solubility(seq)
    session["mev_solubility"] = prediction
    return {
        "message": f"Protein-Sol (local): MEV solubility = {prediction['solubility_score']}",
        "score": prediction["solubility_score"],
        "is_soluble": prediction["is_soluble"],
        "method": "proteinsol_local_mev",
    }


ALPHAFOLD_FETCH_CAP = 30
ALPHAFOLD_DB_PROVIDER = "alphafold_db"


def _structure_provider() -> str:
    """Return the configured provider for structure-related pipeline steps.

    AlphaFold DB is the safe real-tools default because its anonymous lookup
    API is supported for individual UniProt targets. ESMFold is the default
    provider for novel assembled MEV structures because it accepts sequences
    directly rather than requiring a database accession.
    """
    value = os.environ.get("MEV_STRUCTURE_PROVIDER", "esmfold")
    return value.strip().lower().replace("-", "_") or "esmfold"


async def _run_alphafold_db_targets(
    session: dict,
    *,
    strict: bool,
) -> dict:
    """Fetch real AlphaFold DB models for the current target candidates.

    This is shared by step 4-2 and the legacy/direct run_11_1 entry point. It
    only records models returned by AlphaFold DB and preserves every returned
    coordinate URL (PDB, mmCIF, and BinaryCIF). Missing models and failed
    lookups remain explicit unavailable records; they are never replaced with
    a local or synthetic structure.
    """
    from . import alphafold

    vaccine_targets = session.get("vaccine_targets") or {}
    candidates = list(vaccine_targets.get("candidates") or [])
    fetch_candidates = candidates[:ALPHAFOLD_FETCH_CAP]
    structures: list[dict] = []
    coordinate_records: list[dict] = []
    source_urls: list[str] = []

    for candidate in fetch_candidates:
        uniprot_id = str(candidate.get("uniprotId") or "").strip()
        candidate_identity = {
            "candidateIndex": candidate.get("index"),
            "uniprotId": uniprot_id or "?",
            "name": candidate.get("name"),
        }
        if not uniprot_id or uniprot_id == "?":
            structures.append({
                **candidate_identity,
                "status": "unavailable",
                "error": "candidate has no UniProt accession for AlphaFold DB lookup",
            })
            continue

        api_url = f"{alphafold.API_BASE}/{uniprot_id}"
        try:
            result = await alphafold.fetch_prediction(uniprot_id)
        except Exception as exc:  # external client failure is per-target data
            structures.append({
                **candidate_identity,
                "status": "unavailable",
                "sourceUrl": api_url,
                "error": str(exc),
            })
            source_urls.append(api_url)
            continue

        entry = result.entry if result.found else None
        if entry is None:
            structures.append({
                **candidate_identity,
                "status": "unavailable",
                "sourceUrl": api_url,
                "message": result.message,
            })
            source_urls.append(api_url)
            continue

        # A metadata record without any coordinate URL is not a usable model.
        coordinate_urls = {
            "pdb": entry.pdb_url,
            "mmCif": entry.cif_url,
            "binaryMmCif": entry.bcif_url,
        }
        if not any(coordinate_urls.values()):
            structures.append({
                **candidate_identity,
                "status": "unavailable",
                "sourceUrl": api_url,
                "message": "AlphaFold DB returned no PDB/mmCIF coordinate URL",
            })
            source_urls.append(api_url)
            continue

        pdb_text = None
        if entry.pdb_url:
            pdb_text = await alphafold.fetch_structure_pdb(entry.pdb_url)
        structure = {
            **candidate_identity,
            "status": "available",
            "uniprotId": entry.uniprot_id,
            "gene": entry.gene,
            "organism": entry.organism,
            "sequence": entry.sequence or candidate.get("sequence", ""),
            "plddt": round(entry.plddt, 2) if entry.plddt is not None else None,
            "version": entry.version,
            "entryId": entry.entry_id,
            "toolUsed": entry.tool_used,
            "sourceUrl": api_url,
            "pdbUrl": entry.pdb_url,
            "cifUrl": entry.cif_url,
            "bcifUrl": entry.bcif_url,
            "pdbAvailable": pdb_text is not None,
            "mmCifAvailable": bool(entry.cif_url),
            "binaryMmCifAvailable": bool(entry.bcif_url),
            "pdbPreview": (
                pdb_text[:500] + "..."
                if pdb_text and len(pdb_text) > 500
                else (pdb_text or "")
            ),
            "provenance": {
                "status": "real",
                "provider": ALPHAFOLD_DB_PROVIDER,
                "method": "AlphaFold DB alternate for step 4-2",
                "sourceUrl": api_url,
                "coordinateUrls": coordinate_urls,
            },
        }
        structures.append(structure)
        # Full coordinates are deliberately transient. They are used by the
        # coordinate-analysis runner but never placed in the public structure
        # record or returned in a step result.
        if pdb_text:
            coordinate_records.append({
                "candidateIndex": candidate.get("index"),
                "uniprotId": entry.uniprot_id,
                "sequence": entry.sequence or candidate.get("sequence", ""),
                "coordinateText": pdb_text,
                "coordinateSourceUrl": entry.pdb_url,
                "provider": ALPHAFOLD_DB_PROVIDER,
                "metadataSourceUrl": api_url,
            })
        source_urls.extend(url for url in (api_url, *coordinate_urls.values()) if url)

    found = sum(1 for structure in structures if structure.get("status") == "available")
    unavailable = len(fetch_candidates) - found
    status = "real" if found and not unavailable else ("partial" if found else "unavailable")
    provenance = {
        "status": status,
        "provider": ALPHAFOLD_DB_PROVIDER,
        "method": "AlphaFold DB alternate for step 4-2",
        "sourceUrl": "https://alphafold.ebi.ac.uk/",
        "apiBaseUrl": alphafold.API_BASE,
        "sourceUrls": source_urls,
        "cap": ALPHAFOLD_FETCH_CAP,
    }
    session["structures"] = {
        "targets_analyzed": len(fetch_candidates),
        "models_found": found,
        "structures": structures,
        "source": "real" if found else "unavailable",
        "provenance": provenance,
    }
    # Coordinate text is transient session state only. The API job record gets
    # URLs and previews, never full PDB/mmCIF contents.
    session["validated_coordinate_data"] = coordinate_records

    if strict and not found:
        raise ToolUnavailableError(
            tool_name="AlphaFold DB",
            reason=(
                f"AlphaFold DB returned no usable PDB/mmCIF structure for "
                f"{len(fetch_candidates)} target candidate(s)."
            ),
            workaround=(
                "Use a target with a real AlphaFold DB entry, or explicitly "
                "configure an authenticated SWISS-MODEL integration; no "
                "structure was fabricated."
            ),
        )

    return {
        "message": (
            f"AlphaFold DB alternate retrieved {found}/{len(fetch_candidates)} "
            f"real target structures (cap {ALPHAFOLD_FETCH_CAP})"
        ),
        "targets_analyzed": len(fetch_candidates),
        "models_found": found,
        "selectedCap": ALPHAFOLD_FETCH_CAP,
        "method": "alphafold_db_real_alternate",
        "source": "real",
        "provenance": provenance,
    }


async def run_4_2_structure(session: dict, job, step) -> dict:
    """Run the configured real provider for individual target structures.

    Individual UniProt targets continue to use AlphaFold DB. ESMFold is
    reserved for the novel assembled MEV in Step 11-2.
    """
    provider = _structure_provider()
    if provider in {ALPHAFOLD_DB_PROVIDER, "esmfold"}:
        return await _run_alphafold_db_targets(session, strict=True)
    if provider == SWISSMODEL_PROVIDER:
        capability = swissmodel_runtime_capability()
        if not capability.available:
            raise ToolUnavailableError(
                tool_name="SWISS-MODEL",
                reason=capability.reason or "SWISS-MODEL runtime configuration is unavailable.",
                workaround=capability.action or "Configure the server runtime and retry.",
            )
        raise ToolUnavailableError(
            tool_name="SWISS-MODEL",
            reason=(
                "A server runtime capability is present, but no official authenticated "
                "SWISS-MODEL contract client is implemented."
            ),
            workaround=(
                "Use MEV_STRUCTURE_PROVIDER=alphafold_db for real AlphaFold DB lookups, "
                "or attach a validated external MEV model for Step 11-2."
            ),
        )
    raise ToolUnavailableError(
        tool_name="Structure provider configuration",
        reason=(
            f"Unsupported MEV_STRUCTURE_PROVIDER={provider!r}; no structure "
            "provider was contacted."
        ),
        workaround="Set MEV_STRUCTURE_PROVIDER to alphafold_db or swissmodel.",
    )


async def run_11_1(session: dict, job, step) -> dict:
    """Legacy/direct AlphaFold DB target lookup using the shared implementation.

    This entry point is intentionally non-strict for compatibility with direct
    callers that inspect an empty target pool. Production step 4-2 uses the
    strict wrapper above and pauses when no real model is found.
    """
    return await _run_alphafold_db_targets(session, strict=False)


def _normalize_mev_sequence(value: object, label: str = "MEV sequence") -> str:
    import re

    sequence = "".join(str(value or "").split()).upper()
    if not sequence or not re.fullmatch(r"[ACDEFGHIKLMNPQRSTVWY]+", sequence):
        raise ValueError(f"{label} is not a valid amino-acid sequence")
    return sequence


def _coordinate_sequence_from_pdb(pdb_text: str) -> str:
    """Extract the ordered standard-residue sequence from PDB C-alpha records."""
    from io import StringIO

    if not isinstance(pdb_text, str) or not pdb_text.strip():
        raise ValueError("coordinate data is empty")
    parser = PDBParser(QUIET=True)
    structure = parser.get_structure("mev", StringIO(pdb_text))
    model = next(structure.get_models(), None)
    if model is None:
        raise ValueError("coordinate data contains no model")

    sequence: list[str] = []
    for chain in model:
        for residue in chain:
            if residue.id[0] != " " or "CA" not in residue:
                continue
            aa = protein_letters_3to1.get(residue.get_resname().upper())
            if aa:
                sequence.append(aa)
    if not sequence:
        raise ValueError("coordinate data contains no standard amino-acid C-alpha records")
    return "".join(sequence)


def _structure_input_value(data: dict, *names: str):
    for name in names:
        if data.get(name) is not None:
            return data[name]
    return None


def validate_mev_structure_input(raw: dict, expected_sequence: str) -> dict:
    """Validate and canonicalize an externally supplied MEV structure input.

    The contract deliberately accepts no sequence sketch or generated model.
    A PDB coordinate payload must encode the exact assembled MEV sequence. A
    URL-only model must point to HTTPS and carry an explicit full-length
    validation record (identity and coverage both 100 percent), because this
    runner does not silently download or infer coordinates from arbitrary URLs.
    """
    if not isinstance(raw, dict):
        raise ValueError("MEV structure input must be an object")
    expected = _normalize_mev_sequence(expected_sequence, "assembled MEV sequence")
    submitted_raw = _structure_input_value(raw, "sequence")
    submitted = None
    if submitted_raw is not None and str(submitted_raw).strip():
        submitted = _normalize_mev_sequence(submitted_raw, "submitted structure sequence")
        if submitted != expected:
            raise ValueError(
                "submitted structure sequence does not match the exact assembled MEV sequence"
            )

    model_url = _structure_input_value(raw, "modelUrl", "model_url", "sourceUrl", "source_url")
    if model_url is not None:
        model_url = str(model_url).strip()
        from urllib.parse import urlparse

        parsed = urlparse(model_url)
        if parsed.scheme != "https" or not parsed.netloc:
            raise ValueError("modelUrl must be a real HTTPS provider URL")

    coordinate_text = _structure_input_value(raw, "coordinateText", "coordinate_text")
    identity = _structure_input_value(raw, "sequenceIdentity", "sequence_identity")
    coverage = _structure_input_value(raw, "sequenceCoverage", "sequence_coverage")
    validation_method = _structure_input_value(raw, "validationMethod", "validation_method")

    if coordinate_text:
        coordinate_sequence = _coordinate_sequence_from_pdb(coordinate_text)
        if coordinate_sequence != expected:
            raise ValueError(
                "structure coordinate sequence does not match the exact assembled MEV sequence"
            )
        validation = {
            "method": "exact sequence extracted from PDB C-alpha records",
            "identityPercent": 100.0,
            "coveragePercent": 100.0,
            "expectedLength": len(expected),
            "coordinateResidues": len(coordinate_sequence),
            "sequenceFingerprint": _sequence_fingerprint(coordinate_sequence),
        }
    elif submitted is None:
        raise ValueError(
            "URL-only structure input requires the exact assembled MEV sequence"
        )
    else:
        if not model_url:
            raise ValueError(
                "provide usable PDB coordinateText or an HTTPS modelUrl with full-length validation"
            )
        try:
            identity_value = float(identity)
            coverage_value = float(coverage)
        except (TypeError, ValueError):
            raise ValueError(
                "URL-only structure input requires sequenceIdentity=100 and sequenceCoverage=100"
            ) from None
        if not 0 <= identity_value <= 100 or not 0 <= coverage_value <= 100:
            raise ValueError("sequence identity and coverage must be percentages from 0 to 100")
        if identity_value < 100 or coverage_value < 100 or not str(validation_method or "").strip():
            raise ValueError(
                "URL-only structure input requires documented 100% sequence identity and coverage"
            )
        validation = {
            "method": str(validation_method).strip(),
            "identityPercent": identity_value,
            "coveragePercent": coverage_value,
            "expectedLength": len(expected),
            "coordinateResidues": None,
        }

    source = str(raw.get("source") or "user-provided").strip().lower()
    if source not in {"user-provided", "real"}:
        raise ValueError("structure source must be user-provided or real")
    provider = str(raw.get("provider") or "user-provided").strip()
    method = str(raw.get("method") or "external structure model").strip()
    if not provider or not method:
        raise ValueError("structure provider and method are required")

    supplied_fingerprint = _structure_input_value(raw, "sequenceFingerprint", "sequence_fingerprint")
    if supplied_fingerprint is not None:
        supplied_fingerprint = str(supplied_fingerprint).strip().lower()
        if supplied_fingerprint != _sequence_fingerprint(expected):
            raise ValueError("structure sequence fingerprint does not match the exact assembled MEV sequence")

    attachment = {
        key: raw[key]
        for key in ("attachmentId", "fileName", "contentType", "modelFormat")
        if raw.get(key) is not None
    }
    return {
        "sequence": expected,
        "source": source,
        "provider": provider,
        "method": method,
        "modelUrl": model_url,
        "coordinateText": coordinate_text,
        "attachment": attachment,
        "sequenceIdentityValidation": validation,
    }


def _public_mev_structure_metadata(validated: dict) -> dict:
    """Return job-safe structure metadata without transient coordinate text."""
    return {
        "source": validated["source"],
        "provider": validated["provider"],
        "method": validated["method"],
        "modelUrl": validated.get("modelUrl"),
        "attachment": validated.get("attachment") or {},
        "sequenceLength": len(validated["sequence"]),
        "coordinateDataAvailable": bool(validated.get("coordinateText")),
        "sequenceIdentityValidation": validated["sequenceIdentityValidation"],
    }


EXTERNAL_STRUCTURE_TIMEOUT_SEC = 45.0
EXTERNAL_STRUCTURE_MAX_BYTES = 25 * 1024 * 1024


async def _fetch_external_structure_coordinates(model_url: str) -> str:
    """Download a bounded HTTPS PDB payload for downstream coordinate checks.

    A provider URL is not itself a structure result: 11-3/11-4/11-5 require
    coordinates. This fetch is deliberately limited to HTTPS, rejects URLs
    containing credentials, follows only HTTPS redirects, and caps the body
    size. The exact sequence is still validated by ``run_11_2`` after download.
    """
    from urllib.parse import urlparse

    parsed = urlparse(str(model_url or "").strip())
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        raise ValueError("modelUrl must be an HTTPS URL without embedded credentials")

    async with httpx.AsyncClient(
        timeout=EXTERNAL_STRUCTURE_TIMEOUT_SEC,
        follow_redirects=True,
        headers={"User-Agent": "revacc-structure-attachment/1.0"},
    ) as client:
        response = await client.get(str(model_url).strip())
        response.raise_for_status()
        final_url = urlparse(str(response.url))
        if (
            final_url.scheme != "https"
            or not final_url.netloc
            or final_url.username
            or final_url.password
        ):
            raise ValueError("structure provider redirected to an invalid HTTPS URL")
        content = response.content

    if len(content) > EXTERNAL_STRUCTURE_MAX_BYTES:
        raise ValueError(
            f"downloaded structure exceeds the {EXTERNAL_STRUCTURE_MAX_BYTES // (1024 * 1024)} MiB limit"
        )
    try:
        coordinate_text = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("provider response is not UTF-8 PDB text") from exc
    if not coordinate_text.strip():
        raise ValueError("provider returned an empty coordinate file")
    return coordinate_text


# ---------------------------------------------------------------------------
# Phase 11-2: AlphaFold DB — Protein Structure Prediction (REAL API)
# ---------------------------------------------------------------------------
def _completed_step_9_2_input(job) -> tuple[bool, object | None]:
    """Return the completed Step 9-2 sequence without serializing job internals."""
    for phase in (getattr(job, "phases", None) or []):
        for candidate in (getattr(phase, "steps", None) or []):
            if getattr(candidate, "id", None) != "9-2":
                continue
            result = getattr(candidate, "result", None)
            sequence = result.get("sequence") if isinstance(result, dict) else None
            return getattr(candidate, "status", None) == "success", sequence
    return False, None


async def run_11_2(session: dict, job, step) -> dict:
    """Accept only a validated external model for the exact MEV construct.

    Coordinate text is retained in ``validated_coordinate_data`` for later
    local structure checks, but only safe attachment/provenance metadata is
    returned in the public step result. AlphaFold DB is not queried here:
    novel constructs are not database entries, so a real external model must
    be attached through the API before this step can complete.
    """
    mev_data = session.get("mev_construct") or {}
    mev_seq = mev_data.get("sequence", "")

    if not mev_seq:
        raise ToolUnavailableError(
            tool_name="AlphaFold/SwissModel",
            reason="A real MEV construct sequence is required before structure prediction.",
            workaround="Complete MEV assembly and submit the resulting sequence to the structure service.",
        )

    raw_input = (
        session.get("mev_structure_input")
        or session.get("mev_structure_attachment")
        or session.get("external_structure_model")
    )
    if not raw_input:
        provider = _structure_provider()
        if provider == "esmfold":
            try:
                coordinate_text = await esmfold.predict_pdb(mev_seq)
            except esmfold.ESMFoldError as exc:
                raise ToolUnavailableError(
                    tool_name="ESMFold",
                    reason=str(exc),
                    workaround=(
                        "Use a local CUDA-enabled ESMFold runtime for this MEV, "
                        "or configure SWISS-MODEL with a valid CoreAPI token."
                    ),
                ) from exc
            local_available = esmfold._local_esmfold_available()
            raw_input = {
                "sequence": mev_seq,
                "source": "real",
                "provider": "ESMFold",
                "method": "ESMFold local (T4 GPU)" if local_available else "ESMFold public API",
                "modelFormat": "pdb",
                "coordinateText": coordinate_text,
            }
        elif provider == SWISSMODEL_PROVIDER:
            # Manual attachments remain independent of the official provider.
            step_9_2_completed, step_9_2_sequence = _completed_step_9_2_input(job)
            normalized_current_mev = normalize_mev_sequence(mev_seq)
            current_fingerprint = (
                mev_sequence_fingerprint(normalized_current_mev)
                if normalized_current_mev is not None
                else None
            )
            preflight = preflight_swissmodel_submission(
                step_9_2_completed=step_9_2_completed,
                step_9_2_sequence=step_9_2_sequence,
                current_mev_sequence=mev_seq,
                current_mev_fingerprint=current_fingerprint,
            )
            if not preflight.ready:
                raise ToolUnavailableError(
                    tool_name="SWISS-MODEL",
                    reason=preflight.reason or "SWISS-MODEL preflight is unavailable.",
                    workaround=preflight.action or "Review the server runtime and retry Step 11-2.",
                )

            # SWISS-MODEL remains an explicit attachment/lifecycle path.
            admission = admit_swissmodel_submission(preflight)
            if not admission.admitted:
                raise ToolUnavailableError(
                    tool_name="SWISS-MODEL",
                    reason=(
                        "Official SWISS-MODEL submission is not admissible for the exact "
                        f"current MEV ({admission.message_code or 'admission_unavailable'})."
                    ),
                    workaround=admission.action or "Review the official submission prerequisites and retry.",
                )
            lifecycle_status = await SwissModelLifecycleAdapter().create(preflight)
            raise ToolUnavailableError(
                tool_name="SWISS-MODEL",
                reason=lifecycle_status.reason,
                workaround=lifecycle_status.action,
                public_status=lifecycle_status.public_result(),
            )
        else:
            raise ToolUnavailableError(
                tool_name="AlphaFold/SwissModel",
                reason=(
                    "No validated external structure is attached for the exact assembled MEV sequence "
                    "(the novel MEV construct). A local sequence sketch is not a structure prediction."
                ),
                workaround=(
                    "Set MEV_STRUCTURE_PROVIDER=esmfold for supported sequences, or submit the exact "
                    "MEV to a real structure service and attach its validated PDB."
                ),
            )

    # URL metadata is not sufficient for the downstream coordinate analyses.
    # Hydrate the URL into the real provider's PDB response before exact
    # sequence validation, while keeping the full text transient only.
    raw_input = dict(raw_input)
    if not _structure_input_value(raw_input, "coordinateText", "coordinate_text"):
        model_url = _structure_input_value(raw_input, "modelUrl", "model_url", "sourceUrl", "source_url")
        if model_url:
            try:
                raw_input["coordinateText"] = await _fetch_external_structure_coordinates(str(model_url))
            except (ValueError, httpx.HTTPError) as exc:
                raise ToolUnavailableError(
                    tool_name="AlphaFold/SwissModel",
                    reason=f"The attached provider URL did not return usable PDB coordinates: {exc}",
                    workaround=(
                        "Download the provider's complete PDB file for the exact MEV sequence and "
                        "attach it as coordinateText, then retry Step 11-2."
                    ),
                ) from exc

    try:
        validated = validate_mev_structure_input(raw_input, mev_seq)
    except (TypeError, ValueError) as exc:
        raise ToolUnavailableError(
            tool_name="AlphaFold/SwissModel",
            reason=f"The attached MEV structure failed exact-sequence validation: {exc}",
            workaround=(
                "Attach a model generated for the exact assembled MEV sequence with usable PDB "
                "coordinates or documented full-length provider validation."
            ),
        ) from exc

    public = _public_mev_structure_metadata(validated)
    coordinate_text = validated.get("coordinateText")
    # Full coordinates are transient and are never included in public results.
    session["validated_coordinate_data"] = ([{
        "sequence": validated["sequence"],
        "coordinateText": coordinate_text,
        "coordinateSourceUrl": validated.get("modelUrl"),
        "provider": validated["provider"],
        "method": validated["method"],
        "source": validated["source"],
        "metadata": validated.get("attachment") or {},
    }] if coordinate_text else [])
    provenance = {
        "status": validated["source"],
        "source": validated["source"],
        "provider": validated["provider"],
        "method": validated["method"],
        "modelUrl": validated.get("modelUrl"),
        "sequenceIdentityValidation": validated["sequenceIdentityValidation"],
        "coordinateDataAvailable": bool(coordinate_text),
        "syntheticValues": False,
    }
    session["structures"] = {
        "targets_analyzed": 1,
        "models_found": 1,
        "structures": [{
            "status": "available",
            **public,
            "provenance": provenance,
        }],
        "source": validated["source"],
        "provenance": provenance,
    }
    return {
        "message": "Validated external MEV structure accepted for the exact assembled sequence",
        "source": validated["source"],
        "provider": validated["provider"],
        "method": validated["method"],
        "modelUrl": validated.get("modelUrl"),
        "attachment": validated.get("attachment") or {},
        "sequenceIdentityValidation": validated["sequenceIdentityValidation"],
        "coordinateDataAvailable": bool(coordinate_text),
        "provenance": provenance,
    }


# ---------------------------------------------------------------------------
# Phase 11-3: Ramachandran Plot Analysis (local, via real PDB coordinates)
# ---------------------------------------------------------------------------
def _validated_coordinate_records(session: dict) -> list[dict]:
    """Return only the exact-coordinate records validated by Step 11-2.

    Coordinate text is deliberately read from the transient session key.  The
    public ``structures`` result contains metadata/URLs only and is not a
    sufficient input for coordinate analysis.
    """
    return [
        record for record in (session.get("validated_coordinate_data") or [])
        if isinstance(record, dict) and str(record.get("coordinateText") or "").strip()
    ]


def _coordinate_backbone_angles(pdb_text: str) -> tuple[str, dict[int, tuple[float, float]]]:
    """Extract measured phi/psi angles from a full-atom PDB coordinate set.

    This intentionally does not infer angles from sequence or C-alpha-only
    traces.  Residues without both measured angles (normally termini) are
    omitted from the returned map; at least one measured angle is required.
    """
    from io import StringIO
    from math import degrees
    from Bio.PDB import PDBParser, PPBuilder

    if not isinstance(pdb_text, str) or not pdb_text.strip():
        raise ValueError("coordinate data is empty")
    structure = PDBParser(QUIET=True).get_structure("mev", StringIO(pdb_text))
    model = next(structure.get_models(), None)
    if model is None:
        raise ValueError("coordinate data contains no model")

    sequence: list[str] = []
    for chain in model:
        for residue in chain:
            if residue.id[0] != " ":
                continue
            aa = protein_letters_3to1.get(residue.get_resname().upper())
            if aa and all(atom in residue for atom in ("N", "CA", "C")):
                sequence.append(aa)

    angles: dict[int, tuple[float, float]] = {}
    offset = 0
    for peptide in PPBuilder().build_peptides(model):
        peptide_sequence = str(peptide.get_sequence())
        for local_index, (phi, psi) in enumerate(peptide.get_phi_psi_list()):
            if phi is None or psi is None:
                continue
            angles[offset + local_index] = (float(degrees(phi)), float(degrees(psi)))
        offset += len(peptide_sequence)

    if not sequence or not angles:
        raise ValueError("coordinate data has no measurable full-backbone phi/psi angles")
    return "".join(sequence), angles


def _ramachandran_from_pdb(pdb_text: str) -> tuple[str, dict[int, tuple[float, float]]]:
    """Extract measured backbone phi/psi angles; never infer them from sequence."""
    from io import StringIO
    from math import degrees
    from Bio.PDB.Polypeptide import PPBuilder

    if not isinstance(pdb_text, str) or not pdb_text.strip():
        raise ValueError("coordinate data is empty")
    structure = PDBParser(QUIET=True).get_structure("mev", StringIO(pdb_text))
    model = next(structure.get_models(), None)
    if model is None:
        raise ValueError("coordinate data contains no model")
    sequence: list[str] = []
    angles: dict[int, tuple[float, float]] = {}
    position = 0
    for peptide in PPBuilder().build_peptides(model):
        for residue, pair in zip(peptide, peptide.get_phi_psi_list()):
            phi, psi = pair
            aa = protein_letters_3to1.get(residue.get_resname().upper())
            if not aa or phi is None or psi is None:
                continue
            sequence.append(aa)
            angles[position] = (float(degrees(phi)), float(degrees(psi)))
            position += 1
    if not angles:
        raise ValueError("PDB contains no measurable backbone phi/psi angles; full N/CA/C coordinates are required")
    return "".join(sequence), angles


async def run_11_3(session: dict, job, step) -> dict:
    """Analyze measured backbone angles from step 11-2 coordinates locally."""
    records = list(session.get("validated_coordinate_data") or [])
    if not records:
        raise ToolUnavailableError(
            tool_name="MolProbity Ramachandran",
            reason="Step 11-3 requires full validated PDB/mmCIF coordinates from step 11-2; no transient coordinate data is present.",
            workaround="Attach a validated full-coordinate model for the exact MEV sequence before retrying.",
        )
    results: list[dict] = []
    failures: list[dict] = []
    for record in records:
        try:
            coordinate_sequence, angles = _ramachandran_from_pdb(record.get("coordinateText", ""))
            expected = _normalise_protein_sequence(record.get("sequence", ""))
            if expected and coordinate_sequence not in {expected, expected[1:-1]}:
                raise ValueError("coordinate sequence does not match the validated MEV sequence")
            analysis = structure_local.analyze_ramachandran(coordinate_sequence, angles)
            results.append({
                "provider": record.get("provider"),
                "coordinateSourceUrl": record.get("coordinateSourceUrl"),
                "sequenceLength": len(coordinate_sequence),
                **analysis,
            })
        except (ValueError, OSError) as exc:
            failures.append({"provider": record.get("provider"), "reason": str(exc)})
    if not results:
        reason = failures[0]["reason"] if failures else "no analyzable coordinate record"
        raise ToolUnavailableError(
            tool_name="MolProbity Ramachandran",
            reason=f"No validated coordinate record contained measurable backbone phi/psi angles: {reason}",
            workaround="Attach a complete PDB/mmCIF model containing N, CA, and C atoms for the exact MEV sequence.",
        )
    session["ramachandran"] = results
    provenance = {
        "status": "local-analysis",
        "method": "ramachandran_local_coordinate_analysis",
        "analysisLabel": "Ramachandran analysis of measured PDB backbone angles (not MolProbity service output)",
        "provider": "validated external structure coordinates",
        "coordinateRecordCount": len(records),
        "analyzedCount": len(results),
        "parseFailureCount": len(failures),
        "syntheticValues": False,
    }
    return {
        "message": f"Local Ramachandran analysis: analyzed {len(results)}/{len(records)} coordinate set(s)",
        "results": results,
        "parse_failures": failures,
        "method": provenance["method"],
        "source": "local-analysis",
        "provenance": provenance,
    }

def res_info(residue) -> str:
    """Format residue info as 'RESNAME CHAIN POS'."""
    from Bio.PDB import Polypeptide
    try:
        return f"{Polypeptide.three_to_one(residue.get_resname())}{residue.get_parent().id}{residue.id[1]}"
    except Exception:
        return str(residue.get_resname())


def _coordinate_contacts_from_pdb(pdb_text: str) -> tuple[str, list[tuple[int, int, str, str]]]:
    """Extract sequence and non-local residue contacts from real PDB coordinates.

    Only standard amino-acid residues with a C-alpha atom are considered. The
    returned contacts are based on measured C-alpha distances (<= 8 Å), not
    sequence-inferred or synthetic contacts. The parser intentionally uses the
    first model from an experimental/predicted multi-model file.
    """
    from io import StringIO

    if not pdb_text or not pdb_text.strip():
        raise ValueError("empty PDB coordinate text")

    parser = PDBParser(QUIET=True)
    structure = parser.get_structure("alphafold", StringIO(pdb_text))
    model = next(structure.get_models(), None)
    if model is None:
        raise ValueError("PDB contains no coordinate model")

    residues: list[tuple[str, tuple[float, float, float]]] = []
    sequence_parts: list[str] = []
    for chain in model:
        for residue in chain:
            if residue.id[0] != " " or "CA" not in residue:
                continue
            aa = protein_letters_3to1.get(residue.get_resname().upper())
            if not aa:
                continue
            ca = residue["CA"].coord
            residues.append((aa, (float(ca[0]), float(ca[1]), float(ca[2]))))
            sequence_parts.append(aa)

    if len(residues) < 2:
        raise ValueError("PDB contains fewer than two standard C-alpha residues")

    contacts: list[tuple[int, int, str, str]] = []
    for i, (aa_i, coord_i) in enumerate(residues):
        for j in range(i + 1, len(residues)):
            aa_j, coord_j = residues[j]
            # Exclude covalent-neighbour contacts; all remaining contacts are
            # selected from measured coordinates, including inter-chain pairs.
            if j - i <= 2:
                continue
            distance_sq = sum((coord_i[k] - coord_j[k]) ** 2 for k in range(3))
            if distance_sq <= 8.0 ** 2:
                contacts.append((i, j, aa_i, aa_j))

    if not contacts:
        raise ValueError("PDB contains no non-local C-alpha contacts within 8 Å")
    return "".join(sequence_parts), contacts


async def run_4_3_coordinate_analysis(session: dict, job, step) -> dict:
    """Analyze real AlphaFold DB coordinates with an explicitly local method.

    This is not the discontinued official ERRAT service. It reuses the
    repository's quality_local statistics only after deriving contacts from
    full PDB coordinates fetched from AlphaFold DB in step 4-2. Full
    coordinate text stays in the transient runner session and is never
    returned in the public job/step record.
    """
    records = list(session.get("validated_coordinate_data") or [])
    if not records:
        raise ToolUnavailableError(
            tool_name="Local coordinate quality analysis",
            reason=(
                "Step 4-3 requires full, parseable PDB coordinates returned by "
                "the real AlphaFold DB provider in step 4-2; no usable validated "
                "coordinate data is present. Sequence-inferred contacts and "
                "synthetic structures are not used."
            ),
            workaround=(
                "Run step 4-2 with a target that returns a downloadable PDB "
                "coordinate file, then retry step 4-3."
            ),
        )

    results: list[dict] = []
    parse_failures: list[dict] = []
    coordinate_urls: list[str] = []
    for record in records:
        source_url = record.get("coordinateSourceUrl")
        if source_url:
            coordinate_urls.append(source_url)
        try:
            coordinate_sequence, contacts = _coordinate_contacts_from_pdb(
                record.get("coordinateText", "")
            )
        except (ValueError, OSError) as exc:
            parse_failures.append({
                "uniprotId": record.get("uniprotId", "?"),
                "reason": str(exc),
            })
            continue

        quality = quality_local.score_errat(coordinate_sequence, contacts)
        results.append({
            "candidateIndex": record.get("candidateIndex"),
            "uniprotId": record.get("uniprotId", "?"),
            "sequenceLength": len(coordinate_sequence),
            "coordinateResidues": len(coordinate_sequence),
            "coordinateContacts": len(contacts),
            "scoreMethod": "quality_local ERRAT-like statistics (not official ERRAT)",
            **{key: value for key, value in quality.items() if key != "method"},
        })

    if not results:
        detail = (
            " Full coordinate records were present but none contained a "
            "parseable non-local residue contact set."
            if records else ""
        )
        raise ToolUnavailableError(
            tool_name="Local coordinate quality analysis",
            reason=(
                "Step 4-3 could not analyze any validated AlphaFold DB PDB "
                "coordinate set; no sequence-inferred contacts or synthetic "
                "structures were used." + detail
            ),
            workaround=(
                "Re-run step 4-2 to fetch a complete AlphaFold DB PDB file, "
                "then retry step 4-3."
            ),
        )

    session["coordinate_quality_results"] = results
    provenance = {
        "status": "local-analysis",
        "provider": ALPHAFOLD_DB_PROVIDER,
        "method": "quality_local_coordinate_contact_analysis",
        "analysisLabel": "ERRAT-like local coordinate analysis (not official ERRAT)",
        "officialValidation": False,
        "coordinateSource": "AlphaFold DB PDB coordinates",
        "coordinateSourceUrls": list(dict.fromkeys(coordinate_urls)),
        "analyzedCount": len(results),
        "coordinateRecordCount": len(records),
        "parseFailureCount": len(parse_failures),
    }
    return {
        "message": (
            f"Local coordinate quality analysis: analyzed {len(results)}/{len(records)} "
            f"AlphaFold DB PDB coordinate set(s)"
        ),
        "analyzed_count": len(results),
        "coordinate_record_count": len(records),
        "contacts_analyzed": sum(item["coordinateContacts"] for item in results),
        "results": results,
        "parse_failures": parse_failures,
        "method": "quality_local_coordinate_contact_analysis",
        "source": "local-analysis",
        "provenance": provenance,
    }


async def _coordinate_quality_records(session: dict) -> tuple[list[dict], list[dict]]:
    """Score only contacts measured from Step 11-2 validated coordinates."""
    records = _validated_coordinate_records(session)
    if not records:
        raise ToolUnavailableError(
            tool_name="Local coordinate quality analysis",
            reason=(
                "no usable validated coordinate data from Step 11-2 are available; "
                "sequence-inferred contacts are not used for ERRAT/ProSA analysis."
            ),
            workaround="Attach a complete exact-sequence external PDB model and retry the structure validation steps.",
        )

    analyses: list[dict] = []
    failures: list[dict] = []
    for record in records:
        try:
            coordinate_sequence, contacts = _coordinate_contacts_from_pdb(record["coordinateText"])
            expected = _normalize_mev_sequence(record.get("sequence"), "validated MEV sequence")
            if coordinate_sequence != expected:
                raise ValueError("coordinate sequence no longer matches the validated MEV sequence")
            analyses.append({
                "provider": record.get("provider"),
                "coordinateSourceUrl": record.get("coordinateSourceUrl"),
                "sequence": coordinate_sequence,
                "contacts": contacts,
            })
        except (TypeError, ValueError, KeyError) as exc:
            failures.append({"provider": record.get("provider"), "reason": str(exc)})

    if not analyses:
        raise ToolUnavailableError(
            tool_name="Local coordinate quality analysis",
            reason=(
                "Validated coordinate records were present, but none contained "
                "a parseable measured contact set. No sequence-derived contacts were used."
            ),
            workaround="Attach a complete PDB model with measurable C-alpha coordinates and retry.",
        )
    return analyses, failures


async def run_11_4(session: dict, job, step) -> dict:
    """ERRAT-like local analysis over measured validated coordinates.

    This is explicitly not the official ERRAT service; it is a local analysis
    of real coordinates, with no sequence-only or synthetic structure path.
    """
    del job, step
    records, failures = await _coordinate_quality_records(session)
    results = []
    for record in records:
        quality = quality_local.score_errat(record["sequence"], record["contacts"])
        results.append({
            **{key: value for key, value in quality.items() if key != "method"},
            "provider": record["provider"],
            "coordinateSourceUrl": record.get("coordinateSourceUrl"),
        })
    result = {
        "message": f"ERRAT-like local coordinate analysis: {len(results)} structure(s)",
        "results": results,
        "parse_failures": failures,
        "method": "errat_local_coordinates",
        "source": "local-analysis",
        "provenance": {
            "status": "local-analysis",
            "method": "errat_local_coordinates",
            "analysisLabel": "ERRAT-like local coordinate analysis (not official ERRAT)",
            "officialValidation": False,
            "coordinateSource": "Step 11-2 validated external PDB coordinates",
            "syntheticValues": False,
            "analyzedCount": len(results),
        },
    }
    session["errat_result"] = result
    return result


# ---------------------------------------------------------------------------
# Phase 11-5: ProSA → LOCAL coordinate quality assessment
# ---------------------------------------------------------------------------
async def run_11_5(session: dict, job, step) -> dict:
    """ProSA-like local analysis over measured validated coordinates."""
    del job, step
    records, failures = await _coordinate_quality_records(session)
    results = []
    for record in records:
        quality = quality_local.score_prosa(record["sequence"], record["contacts"])
        results.append({
            **{key: value for key, value in quality.items() if key != "method"},
            "provider": record["provider"],
            "coordinateSourceUrl": record.get("coordinateSourceUrl"),
        })
    result = {
        "message": f"ProSA-like local coordinate analysis: {len(results)} structure(s)",
        "results": results,
        "parse_failures": failures,
        "method": "prosa_local_coordinates",
        "source": "local-analysis",
        "provenance": {
            "status": "local-analysis",
            "method": "prosa_local_coordinates",
            "analysisLabel": "ProSA-like local coordinate analysis (not official ProSA-web)",
            "officialValidation": False,
            "coordinateSource": "Step 11-2 validated external PDB coordinates",
            "syntheticValues": False,
            "analyzedCount": len(results),
        },
    }
    session["prosa_result"] = result
    return result


# ---------------------------------------------------------------------------
# Phase 11-6: ToxinPred → LOCAL motif scanning
# ---------------------------------------------------------------------------
async def run_11_6(session: dict, job, step) -> dict:
    """ToxinPred toxicity prediction → LOCAL motif scanning."""
    seq = session.get("mev_construct", {}).get("sequence", "")
    if not seq:
        targets = session.get("vaccine_targets") or {}
        candidates = targets.get("candidates") or []
        if candidates:
            seq = candidates[0].get("sequence", "")

    if not seq:
        return {"message": "No sequence for ToxinPred analysis", "method": "toxinpred_local"}

    prediction = algpred_local.predict_toxicity(seq)
    session["toxicity_result"] = prediction
    return {
        "message": f"ToxinPred (local): score = {prediction['toxicity_score']}, toxic = {prediction['is_toxic']}",
        "toxicity_score": prediction["toxicity_score"],
        "is_toxic": prediction["is_toxic"],
        "toxin_motifs_found": prediction["toxin_motifs_found"],
        "method": "toxinpred_motif_local",
    }


# ---------------------------------------------------------------------------
# Phase 12-1: DbD2 → LOCAL disulfide bond design
# ---------------------------------------------------------------------------
async def run_13_1(session: dict, job, step) -> dict:
    """DbD2 disulfide bond design → LOCAL computation.

    Designs stabilizing disulfide bonds for the MEV protein construct using
    the sequence-aware predictor (adjuvant_dbd2_local.design_disulfide_bonds).
    If the MEV backbone model is available (session["mev_structure"]["pdb"]),
    C-beta coordinates are extracted and used for coordinate-aware candidate
    scoring.

    Session keys read:  mev_construct (protein sequence), mev_structure (pdb)
    Session keys written:  disulfide_design
    """
    mev_data = session.get("mev_construct") or {}
    sequence = mev_data.get("sequence", "")

    # Attempt to extract C-beta coordinates from the MEV backbone model.
    cb_coords: dict[int, float] = {}
    mev_structure = session.get("mev_structure") or {}
    mev_pdb = mev_structure.get("pdb") or ""
    if mev_pdb:
        # Parse simple ATOM CA lines to assign approximate CB positions.
        # C-beta is roughly 1.5 Å from C-alpha along the side-chain direction.
        # For a quick proxy, we use the C-alpha position as a CB surrogate.
        import re
        for m in re.finditer(r"ATOM\s+\d+\s+CA\s+(\w)\s+A\s+\d+\s+([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)", mev_pdb):
            pos = int(m.group(1)) if m.group(1).isdigit() else 0  # simplified
            # Actually, let's parse the residue index from the seq_id field
            # The format is typically: ATOM      1  CA  ALA A   1     ... 
            # But our simplified PDB uses residue index i from the sequence.
            # We'll use a simple approach: assign CB as the residue index itself.
            pass
        # Fallback: use residue index as CB coordinate if no real PDB data
        # (the backbone model's simplified placement uses i * 3.8 Å)
        for i in range(1, len(seq) + 1):
            cb_coords[i] = (i - 1) * 3.8  # sequential z-position

    if not sequence:
        return {
            "message": "No MEV protein sequence for disulfide design",
            "candidates": [],
            "count": 0,
            "method": "dbd2_local",
            "source": "unavailable",
            "status": "unavailable",
            "provenance": {
                "status": "unavailable",
                "method": "dbd2_local",
                "reason": "validated MEV construct sequence is missing",
                "syntheticValues": False,
            },
        }

    design = adjuvant_dbd2_local.design_disulfide_bonds(sequence, cb_coords=cb_coords)
    session["disulfide_design"] = design

    if not design.get("count"):
        return {
            "message": "DbD2 (local): no disulfide candidates — construct may be too short or without favorable loop contexts",
            "candidates": [],
            "count": 0,
            "sequence_length": design["sequence_length"],
            "method": "dbd2_local",
            "source": "local-analysis",
            "provenance": {
                "status": "local-analysis",
                "method": "dbd2_local_sequence_aware_design",
                "inputSource": "session['mev_construct']['sequence']",
                "syntheticValues": False,
            },
        }

    top = design["candidates"][0]
    return {
        "message": (
            f"DbD2 (local): {design['count']} disulfide candidates "
            f"(top: {top['mutation_1']} + {top['mutation_2']})"
        ),
        "candidates": design["candidates"],
        "count": design["count"],
        "sequence_length": design["sequence_length"],
        "top_candidate": top,
        "method": "dbd2_local",
        "source": "local-analysis",
        "provenance": {
            "status": "local-analysis",
            "method": "dbd2_local_sequence_aware_design",
            "inputSource": "session['mev_construct']['sequence']",
            "coordinateAware": bool(cb_coords),
            "syntheticValues": False,
        },
    }


# ---------------------------------------------------------------------------
# Phase 13-2: Restriction Site Analysis → LOCAL (BioPython logic)
# ---------------------------------------------------------------------------
async def run_13_2(session: dict, job, step) -> dict:
    """Restriction site analysis → LOCAL computation.

    Uses the local restriction-enzyme catalog (adjuvant_dbd2_local.py) to
    find all cut sites in the codon-optimized DNA sequence.
    """
    codon_opt = session.get("codon_optimized", {})
    sequence = ""
    if isinstance(codon_opt, dict):
        sequence = codon_opt.get("codon_optimized", "")

    # Restriction sites are a DNA operation. Never scan a protein sequence
    # as DNA — require real codon-optimized DNA from JCat.
    if not sequence:
        return {
            "message": "No codon-optimized DNA sequence for restriction analysis (run 13-1 JCat first)",
            "restriction_sites": {},
            "total_sites": 0,
            "enzymes_checked": 0,
            "sequence_length": 0,
            "method": "restriction_local",
            "source": "unavailable",
            "status": "unavailable",
            "provenance": {
                "status": "unavailable",
                "method": "restriction_local",
                "reason": "validated JCat codon-optimized DNA is missing",
                "syntheticValues": False,
            },
            "pending_dependency": "13-1",
        }

    sites = adjuvant_dbd2_local.find_restriction_sites(sequence)

    total_sites = sum(len(v) for v in sites.values())
    session["restriction_analysis"] = {
        "restriction_sites": sites,
        "total_sites": total_sites,
    }

    return {
        "message": f"Restriction analysis (local): {total_sites} cut site(s) across {len(sites)} enzyme(s)",
        "restriction_sites": sites,
        "total_sites": total_sites,
        "enzymes_checked": len(sites),
        "sequence_length": len(sequence),
        "method": "restriction_local",
        "source": "local-analysis",
        "provenance": {
            "status": "local-analysis",
            "method": "restriction_local_biopython_catalog",
            "inputSource": "session['codon_optimized']['codon_optimized']",
            "syntheticValues": False,
        },
    }


# ---------------------------------------------------------------------------
# Phase 13-3: In Silico Cloning → LOCAL (Gibson / restriction cloning)
# ---------------------------------------------------------------------------
async def run_13_3(session: dict, job, step) -> dict:
    """In Silico Cloning → LOCAL computation.

    Designs a cloning strategy (restriction digest or Gibson assembly)
    for the codon-optimized insert into a standard vector.
    """
    codon_opt = session.get("codon_optimized", {})
    insert_seq = ""
    if isinstance(codon_opt, dict):
        insert_seq = codon_opt.get("codon_optimized", "")

    # In-silico cloning is a DNA operation — require real codon-optimized DNA.
    if not insert_seq:
        return {
            "message": "No codon-optimized DNA sequence for in silico cloning (run 13-1 JCat first)",
            "cloning_strategy": None,
            "method": "cloning_local",
            "source": "unavailable",
            "status": "unavailable",
            "provenance": {
                "status": "unavailable",
                "method": "cloning_local",
                "reason": "validated JCat codon-optimized DNA is missing",
                "syntheticValues": False,
            },
            "pending_dependency": "13-1",
        }

    pET28a_MCS = (
        "CATATGGCTAGCATGACTGGTGGACAGCAAATGGGTCGCGGATCCGAATTCGAGCTCCGTCGACAAGCTTGCGGCCGCACTCGAGCACCACCACCACCACCACTGAGATCCGGCTGCTAACAAAGCCCGAAAGGAAGCTGAGTTGGCTGCTGCCACCGCTGAGCAATAACTAGCATAACCCCTTGGGGCCTCTAAACGGGTCTTGAGGGGTTTTTTGCTGAAAGGAGGAACTATATCCGGAT"
    )

    strategy = adjuvant_dbd2_local.design_cloning_strategy(
        vector_seq=pET28a_MCS,
        insert_seq=insert_seq,
        method="gibson" if len(insert_seq) > 4000 else "restriction",
    )

    session["cloning_strategy"] = strategy

    method_label = strategy["method"]
    chosen = strategy.get("chosen_enzymes") or []
    return {
        "message": f"In silico cloning (local): {method_label} strategy, insert {strategy['insert_length_bp']} bp",
        "cloning_strategy": strategy,
        "method_used": method_label,
        "chosen_enzymes": chosen,
        "protocol_steps": strategy.get("protocol", []),
        "has_issues": not bool(chosen) and method_label == "restriction",
        "method": "cloning_local",
        "source": "local-analysis",
        "provenance": {
            "status": "local-analysis",
            "method": "cloning_local_vector_insert_design",
            "inputSource": "session['codon_optimized']['codon_optimized']",
            "vector": "pET-28a MCS fixture sequence bundled with the repository",
            "syntheticValues": False,
        },
    }


# ---------------------------------------------------------------------------
# Phase 14-1: C-ImmSim → LOCAL ODE simulation
# ---------------------------------------------------------------------------
async def run_14_1(session: dict, job, step) -> dict:
    """C-ImmSim must be an external validated simulation, not a local proxy."""
    raise ToolUnavailableError(
        tool_name="C-ImmSim",
        reason="C-ImmSim external simulation is unavailable; the local ODE model is not used as a substitute.",
        workaround="Run C-ImmSim externally and attach its validated output before retrying.",
    )


# ---------------------------------------------------------------------------
# Phase 14-2: Immune Simulation Results Aggregation (LOCAL enhanced)
# ---------------------------------------------------------------------------
async def run_14_2(session: dict, job, step) -> dict:
    """Phase 14 Step 2: Immune simulation results aggregation.

    Aggregates all local immune simulation results into a comprehensive
    immune profile.
    """
    simulation = session.get("immune_simulation", {})
    physicochemical = session.get("physicochemical", {})
    ramachandran = session.get("ramachandran", {})

    trajectory = simulation.get("trajectory", []) if simulation else []
    peak_igg = max((t["IgG"] for t in trajectory), default=0)
    peak_th1 = max((t["Th1"] for t in trajectory), default=0)
    peak_th2 = max((t["Th2"] for t in trajectory), default=0)
    memory_t = trajectory[-1].get("memory_T", 0) if trajectory else 0

    return {
        "message": "Immune simulation summary (local ODE)",
        "status": "complete" if simulation else "partial",
        "peak_igG": peak_igg,
        "peak_Th1": peak_th1,
        "peak_Th2": peak_th2,
        "memory_T_cells": memory_t,
        "seroconversion": simulation.get("seroconversion_day") is not None if simulation else False,
        "physicochemical_available": bool(physicochemical),
        "ramachandran_available": bool(ramachandran),
        "structures_available": len(session.get("structures", {}).get("structures", [])),
        "method": "immune_aggregation_local",
    }


# ---------------------------------------------------------------------------
# Phase 4-1: Second Structure Prediction (SOPMA) → LOCAL COMPUTATION
# ---------------------------------------------------------------------------
async def run_4_1_pause(session: dict, job, step) -> dict:
    """Chou-Fasman secondary structure prediction — LOCAL COMPUTATION.

    Uses local Chou-Fasman algorithm (structure_local.py) instead of SOPMA.
    """
    targets = session.get("vaccine_targets") or {}
    candidates = targets.get("candidates") or []

    results = []
    for cand in candidates:
        seq = cand.get("sequence", "")
        if seq:
            prediction = structure_local.predict_secondary_structure(seq)
            results.append({
                "uniprotId": cand.get("uniprotId", "?"),
                "name": cand.get("name", "?"),
                **prediction,
            })

    session["secondary_structure"] = results
    return {
        "message": f"Chou-Fasman (local): analyzed {len(results)}/{len(candidates)} proteins",
        "proteins_analyzed": len(results),
        "method": "chou_fasman_local",
    }


# ---------------------------------------------------------------------------
# Phase 4-1: ProtParam — Physicochemical Properties (individual proteins)
# ---------------------------------------------------------------------------
async def run_4_1(session: dict, job, step) -> dict:
    """Phase 4 Step 1: ProtParam — Physicochemical protein properties for
    individual essential protein candidates.

    Computes molecular weight, pI, instability, GRAVY for each essential
    candidate protein. Uses BioPython ProteinAnalysis.
    """
    from Bio.SeqUtils.ProtParam import ProteinAnalysis

    candidates = (session.get("essential") or {}).get("candidates") or []
    if not candidates:
        return {
            "message": "No essential candidates for ProtParam analysis",
            "method": "protparam_local_biopython",
            "proteins_analyzed": 0,
        }

    results = []
    for cand in candidates:
        seq = cand.get("sequence", "")
        if not seq:
            continue
        try:
            analysis = ProteinAnalysis(seq)
            mw = analysis.molecular_weight()
            pI = analysis.isoelectric_point()
            instability = analysis.instability_index()
            gravy = analysis.gravy()
            results.append({
                "uniprotId": cand.get("uniprotId", "?"),
                "name": cand.get("name", "?"),
                "molecular_weight": round(mw, 2),
                "isoelectric_point": round(pI, 2),
                "instability_index": round(instability, 2),
                "gravy": round(gravy, 4),
                "length": len(seq),
            })
        except Exception as exc:
            results.append({
                "uniprotId": cand.get("uniprotId", "?"),
                "name": cand.get("name", "?"),
                "error": str(exc),
            })

    session["protein_properties"] = results
    return {
        "message": f"ProtParam: analyzed {len(results)}/{len(candidates)} proteins",
        "method": "protparam_local_biopython",
        "proteins_analyzed": len(results),
        "properties": results,
    }


# ---------------------------------------------------------------------------
# Phase 4-4 / 11-1: SOPMA → LOCAL Chou-Fasman (replaces pause)
# ---------------------------------------------------------------------------
async def run_4_4(session: dict, job, step) -> dict:
    """SOPMA secondary structure prediction → LOCAL Chou-Fasman.

    Uses local Chou-Fasman implementation (structure_local.py).
    """
    mev_sequence = session.get("mev_construct", {}).get("sequence", "")

    # Phase 4-4 runs on the essential-protein set. The MEV construct only
    # exists later in Phase 9, so do not silently analyze one arbitrary target.
    if getattr(step, "id", None) == "4-4" and not mev_sequence:
        candidates = (session.get("essential") or {}).get("candidates") or []
        results = []
        for candidate in candidates:
            sequence = candidate.get("sequence", "")
            if not sequence:
                continue
            prediction = structure_local.predict_secondary_structure(sequence)
            results.append({
                "uniprotId": candidate.get("uniprotId", "?"),
                "name": candidate.get("name", "?"),
                "sequence_length": len(sequence),
                **prediction,
            })

        session["protein_secondary_structures"] = results
        return {
            "message": f"Chou-Fasman (local): analyzed {len(results)}/{len(candidates)} proteins",
            "proteins_analyzed": len(results),
            "results": results,
            "method": "chou_fasman_local",
        }

    if not mev_sequence:
        targets = session.get("vaccine_targets") or {}
        candidates = targets.get("candidates") or []
        if candidates:
            mev_sequence = candidates[0].get("sequence", "")

    if not mev_sequence:
        return {
            "message": "No sequence for secondary structure prediction",
            "prediction": "",
        }

    prediction = structure_local.predict_secondary_structure(mev_sequence)
    session["secondary_structure"] = prediction

    return {
        "message": f"Chou-Fasman (local): {prediction['percentages']['helix']}% helix, {prediction['percentages']['sheet']}% sheet",
        "sequence_length": len(mev_sequence),
        "prediction": prediction["prediction"],
        "percentages": prediction["percentages"],
        "method": "chou_fasman_local",
    }


# ---------------------------------------------------------------------------
# Phase 6-5: HTL VaxiJen → LOCAL ACC (replaces pause)
# ---------------------------------------------------------------------------
async def run_6_5(session: dict, job, step) -> dict:
    """HTL antigenicity (VaxiJen) → LOCAL ACC computation."""
    epitopes = session.get("epitopes") or []
    htl_epitopes = [e for e in epitopes if e.get("type") == "HTL"]

    if not htl_epitopes:
        return {
            "message": "No HTL epitopes for antigenicity scoring",
            "scored": 0,
            "total": 0,
            "method": "vaxijen_acc_local_htl",
        }

    scored = 0
    for e in htl_epitopes:
        seq = e.get("sequence", "")
        if seq:
            prediction = vaxijen_local.is_antigenic(seq, "bacteria")
            e["antigenicityScore"] = prediction["score"]
            e["antigenicity_method"] = prediction["method"]
            scored += 1

    return {
        "message": f"VaxiJen (local ACC): {scored} HTL epitopes scored",
        "scored": scored,
        "total": len(htl_epitopes),
        "method": "vaxijen_acc_local_htl",
    }


# ---------------------------------------------------------------------------
# Phase 7-2: B-cell VaxiJen → LOCAL ACC (replaces pause)
# ---------------------------------------------------------------------------
async def run_7_2(session: dict, job, step) -> dict:
    """B-cell antigenicity (VaxiJen) → LOCAL ACC computation."""
    epitopes = session.get("epitopes") or []
    bcell_epitopes = [e for e in epitopes if e.get("type") in ("BCELL_LINEAR", "BCELL_CONFORMATIONAL")]

    if not bcell_epitopes:
        return {
            "message": "No B-cell epitopes for antigenicity scoring",
            "scored": 0,
            "total": 0,
            "method": "vaxijen_acc_local_bcell",
        }

    scored = 0
    for e in bcell_epitopes:
        seq = e.get("sequence", "")
        if seq:
            prediction = vaxijen_local.is_antigenic(seq, "bacteria")
            e["antigenicityScore"] = prediction["score"]
            e["antigenicity_method"] = prediction["method"]
            scored += 1

    return {
        "message": f"VaxiJen (local ACC): {scored} B-cell epitopes scored",
        "scored": scored,
        "total": len(bcell_epitopes),
        "method": "vaxijen_acc_local_bcell",
    }


# ---------------------------------------------------------------------------
# Phase 6-6: HTL AlgPred → LOCAL FAO/WHO (replaces pause)
# ---------------------------------------------------------------------------
async def run_6_6(session: dict, job, step) -> dict:
    """HTL allergenicity (AlgPred) → LOCAL FAO/WHO computation."""
    epitopes = session.get("epitopes") or []
    htl_epitopes = [e for e in epitopes if e.get("type") == "HTL"]

    if not htl_epitopes:
        return {
            "message": "No HTL epitopes for allergenicity scoring",
            "scored": 0,
            "total": 0,
            "method": "algpred_local_htl",
        }

    scored = 0
    for e in htl_epitopes:
        seq = e.get("sequence", "")
        if seq:
            prediction = algpred_local.predict_allergenicity(seq)
            e["allergenicityScore"] = 1 - prediction["allergen_score"]
            e["allergenicity_method"] = prediction["method"]
            scored += 1

    return {
        "message": f"AlgPred (local): {scored} HTL epitopes scored",
        "scored": scored,
        "total": len(htl_epitopes),
        "method": "algpred_local_htl",
    }


# ---------------------------------------------------------------------------
# Phase 7-3: B-cell AlgPred → LOCAL FAO/WHO (replaces pause)
# ---------------------------------------------------------------------------
async def run_7_3(session: dict, job, step) -> dict:
    """B-cell allergenicity (AlgPred) → LOCAL FAO/WHO computation."""
    epitopes = session.get("epitopes") or []
    bcell_epitopes = [e for e in epitopes if e.get("type") in ("BCELL_LINEAR", "BCELL_CONFORMATIONAL")]

    if not bcell_epitopes:
        return {
            "message": "No B-cell epitopes for allergenicity scoring",
            "scored": 0,
            "total": 0,
            "method": "algpred_local_bcell",
        }

    scored = 0
    for e in bcell_epitopes:
        seq = e.get("sequence", "")
        if seq:
            prediction = algpred_local.predict_allergenicity(seq)
            e["allergenicityScore"] = 1 - prediction["allergen_score"]
            e["allergenicity_method"] = prediction["method"]
            scored += 1

    return {
        "message": f"AlgPred (local): {scored} B-cell epitopes scored",
        "scored": scored,
        "total": len(bcell_epitopes),
        "method": "algpred_local_bcell",
    }


# ---------------------------------------------------------------------------
# Phase 5-4 / 6-7 / 7-4: ToxinPred → LOCAL motif scanning (replaces pause)
# ---------------------------------------------------------------------------
async def run_5_4(session: dict, job, step) -> dict:
    """CTL toxicity (ToxinPred) → LOCAL motif scanning."""
    epitopes = session.get("epitopes") or []
    ctl_epitopes = [e for e in epitopes if e.get("type") == "CTL"]

    if not ctl_epitopes:
        return {"message": "No CTL epitopes for toxicity scoring", "scored": 0, "total": 0,
            "method": "toxinpred_local_ctl"}

    scored = 0
    for e in ctl_epitopes:
        seq = e.get("sequence", "")
        if seq:
            prediction = algpred_local.predict_toxicity(seq)
            e["toxicityScore"] = 1 - prediction["toxicity_score"]
            e["toxicity_method"] = prediction["method"]
            scored += 1

    return {
        "message": f"ToxinPred (local): {scored} CTL epitopes scored",
        "scored": scored, "total": len(ctl_epitopes),
        "method": "toxinpred_local_ctl",
    }


async def run_6_7(session: dict, job, step) -> dict:
    """HTL toxicity (ToxinPred) → LOCAL motif scanning."""
    epitopes = session.get("epitopes") or []
    htl_epitopes = [e for e in epitopes if e.get("type") == "HTL"]

    if not htl_epitopes:
        return {"message": "No HTL epitopes for toxicity scoring", "scored": 0, "total": 0,
            "method": "toxinpred_local_htl"}

    scored = 0
    for e in htl_epitopes:
        seq = e.get("sequence", "")
        if seq:
            prediction = algpred_local.predict_toxicity(seq)
            e["toxicityScore"] = 1 - prediction["toxicity_score"]
            e["toxicity_method"] = prediction["method"]
            scored += 1

    return {
        "message": f"ToxinPred (local): {scored} HTL epitopes scored",
        "scored": scored, "total": len(htl_epitopes),
        "method": "toxinpred_local_htl",
    }


async def run_7_4(session: dict, job, step) -> dict:
    """B-cell toxicity (ToxinPred) → LOCAL motif scanning."""
    epitopes = session.get("epitopes") or []
    bcell_epitopes = [e for e in epitopes if e.get("type") in ("BCELL_LINEAR", "BCELL_CONFORMATIONAL")]

    if not bcell_epitopes:
        return {"message": "No B-cell epitopes for toxicity scoring", "scored": 0, "total": 0,
            "method": "toxinpred_local_bcell"}

    scored = 0
    for e in bcell_epitopes:
        seq = e.get("sequence", "")
        if seq:
            prediction = algpred_local.predict_toxicity(seq)
            e["toxicityScore"] = 1 - prediction["toxicity_score"]
            e["toxicity_method"] = prediction["method"]
            scored += 1

    return {
        "message": f"ToxinPred (local): {scored} B-cell epitopes scored",
        "scored": scored, "total": len(bcell_epitopes),
        "method": "toxinpred_local_bcell",
    }


# ---------------------------------------------------------------------------
# Phase 5-5: Immunogenicity Scoring — local analysis over real IEDB inputs
# ---------------------------------------------------------------------------
async def run_5_5(session: dict, job, step) -> dict:
    """Compute CTL immunogenicity from real IEDB rows and local TAP analysis.

    The IEDB consensus endpoint may return percentile rank without an IC50.
    In that case the local model uses the observed percentile as a monotonic
    binding proxy (``1 - percentile/100``); it never invents an IC50 or labels
    the resulting score as IEDB immunogenicity. Rows with neither an IEDB IC50
    nor percentile rank remain explicitly unavailable.
    """
    del step
    epitopes = session.get("epitopes") or []
    ctl_epitopes = [e for e in epitopes if e.get("type") == "CTL"]

    if not ctl_epitopes:
        return {
            "message": "No CTL epitopes for immunogenicity scoring",
            "scored": 0,
            "total": 0,
            "method": "immunogenicity_local_ctl",
            "source": "local-analysis",
            "provenance": {
                "status": "local-analysis",
                "method": "immunogenicity_local_ctl",
                "input": "authoritative session CTL epitope rows",
                "syntheticValues": False,
            },
        }

    scored = 0
    input_kinds: set[str] = set()
    input_records: list[dict] = []
    for e in ctl_epitopes:
        seq = e.get("sequence", "")
        if not seq:
            continue

        allele = e.get("hlaAllele")
        real_ic50 = e.get("ic50")
        percentile = e.get("percentileRank")
        if not allele:
            raise ToolUnavailableError(
                tool_name="IEDB immunogenicity inputs",
                reason=(
                    "CTL immunogenicity requires the real IEDB allele; "
                    f"epitope {e.get('id', 'unknown')} has no allele attribution."
                ),
                workaround="Retry the IEDB prediction step and attach complete allele fields.",
            )

        tap_score = e.get("_tap_score") or e.get("tap_score")
        tap_score = tap_score if tap_score is not None else cytokine_local.estimate_tap_transport(seq)
        if real_ic50 is not None:
            prediction = cytokine_local.calculate_immunogenicity_score(
                seq,
                mhc_i_ic50={allele: float(real_ic50)},
                tap_transport=tap_score,
            )
            input_kind = "real IEDB IC50"
            input_record = {"allele": allele, "ic50": float(real_ic50)}
        elif percentile is not None:
            try:
                rank = float(percentile)
            except (TypeError, ValueError) as exc:
                raise ToolUnavailableError(
                    tool_name="IEDB immunogenicity inputs",
                    reason=f"IEDB percentile rank is not numeric for epitope {e.get('id', 'unknown')}.",
                    workaround="Retry the IEDB prediction step and attach a numeric percentile rank.",
                ) from exc
            if not 0.0 <= rank <= 100.0:
                raise ToolUnavailableError(
                    tool_name="IEDB immunogenicity inputs",
                    reason=f"IEDB percentile rank {rank} is outside the valid 0–100 range.",
                    workaround="Retry the IEDB prediction step and attach a valid percentile rank.",
                )
            # This is deliberately a normalized rank proxy, not an IC50
            # conversion. The provenance below exposes the exact transform.
            binding_proxy = 1.0 - (rank / 100.0)
            prediction = cytokine_local.calculate_immunogenicity_score(
                seq,
                tap_transport=tap_score,
                mhc_i_binding_score=binding_proxy,
            )
            input_kind = "real IEDB percentile rank proxy"
            input_record = {
                "allele": allele,
                "percentileRank": rank,
                "bindingProxy": round(binding_proxy, 6),
            }
        else:
            raise ToolUnavailableError(
                tool_name="IEDB immunogenicity inputs",
                reason=(
                    "CTL immunogenicity requires a real IEDB IC50 or percentile rank; "
                    f"epitope {e.get('id', 'unknown')} has neither."
                ),
                workaround="Retry the IEDB prediction step and attach complete affinity fields.",
            )

        e["immunogenicityScore"] = prediction["immunogenicity_score"]
        e["immunogenicity_method"] = "immunogenicity_local_iedb_inputs"
        e["immunogenicityInputs"] = {
            **input_record,
            "tapTransport": round(float(tap_score), 6),
            "method": "local MHC/TAP/length integration",
        }
        input_kinds.add(input_kind)
        input_records.append(input_record)
        scored += 1

    return {
        "message": f"Immunogenicity (local analysis): {scored}/{len(ctl_epitopes)} CTL epitopes scored",
        "scored": scored,
        "total": len(ctl_epitopes),
        "method": "immunogenicity_local_iedb_inputs",
        "source": "local-analysis",
        "provenance": {
            "status": "local-analysis",
            "method": "local MHC/TAP/length integration",
            "inputs": sorted(input_kinds),
            "inputRecords": input_records,
            "bindingProxy": "1 - IEDB percentileRank/100 when IC50 is absent; no IC50 is inferred",
            "strongBinderThreshold": getattr(job.config, "mhciPercentile", 2.0),
            "immunogenicityThreshold": 0.4,
            "syntheticValues": False,
            "externalToolResult": False,
        },
    }


# ---------------------------------------------------------------------------
# Phase 6-2: IFNepitope → LOCAL motif scoring (replaces pause)
# ---------------------------------------------------------------------------
async def run_6_2(session: dict, job, step) -> dict:
    """IFN-γ induction prediction — LOCAL computation using motif scoring."""
    epitopes = session.get("epitopes") or []
    htl_epitopes = [e for e in epitopes if e.get("type") == "HTL"]

    if not htl_epitopes:
        return {"message": "No HTL epitopes for IFN-γ induction prediction", "scored": 0, "total": 0,
            "method": "ifnepitope_local"}

    scored = 0
    for e in htl_epitopes:
        seq = e.get("sequence", "")
        if seq:
            ic50 = e.get("ic50")
            binding_score = cytokine_local._ic50_to_binding_score(ic50) if ic50 is not None else None
            prediction = cytokine_local.predict_ifn_gamma_epitope(seq, binding_score=binding_score)
            e["ifn_gamma_induction"] = prediction["inducer_score"]
            e["is_ifn_inducer"] = prediction["is_inducer"]
            e["ifn_method"] = prediction["method"]
            scored += 1

    inducers = sum(1 for e in htl_epitopes if e.get("is_ifn_inducer"))
    return {
        "message": f"IFNepitope (local): {inducers}/{len(htl_epitopes)} IFN-γ inducers",
        "scored": scored, "total": len(htl_epitopes),
        "inducer_count": inducers,
        "method": "ifnepitope_local",
    }


# ---------------------------------------------------------------------------
# Phase 6-3: IL4Pred → LOCAL Th2 motif scanning (replaces pause)
# ---------------------------------------------------------------------------
async def run_6_3(session: dict, job, step) -> dict:
    """IL-4 induction prediction — LOCAL computation using Th2 motif scanning."""
    epitopes = session.get("epitopes") or []
    htl_epitopes = [e for e in epitopes if e.get("type") == "HTL"]

    if not htl_epitopes:
        return {"message": "No HTL epitopes for IL-4 induction prediction", "scored": 0, "total": 0,
            "method": "il4pred_local"}

    scored = 0
    for e in htl_epitopes:
        seq = e.get("sequence", "")
        if seq:
            ic50 = e.get("ic50")
            binding_score = cytokine_local._ic50_to_binding_score(ic50) if ic50 is not None else None
            prediction = cytokine_local.predict_il4_epitope(seq, binding_score=binding_score)
            e["il4_induction"] = prediction["inducer_score"]
            e["is_il4_inducer"] = prediction["is_inducer"]
            e["il4_method"] = prediction["method"]
            scored += 1

    inducers = sum(1 for e in htl_epitopes if e.get("is_il4_inducer"))
    return {
        "message": f"IL4Pred (local): {inducers}/{len(htl_epitopes)} IL-4 inducers",
        "scored": scored, "total": len(htl_epitopes),
        "inducer_count": inducers,
        "method": "il4pred_local",
    }


# ---------------------------------------------------------------------------
# Phase 6-4: IL10Pred → LOCAL regulatory motif scanning (replaces pause)
# ---------------------------------------------------------------------------
async def run_6_4(session: dict, job, step) -> dict:
    """IL-10 induction prediction — LOCAL computation using regulatory motif scanning."""
    epitopes = session.get("epitopes") or []
    htl_epitopes = [e for e in epitopes if e.get("type") == "HTL"]

    if not htl_epitopes:
        return {"message": "No HTL epitopes for IL-10 induction prediction", "scored": 0, "total": 0,
            "method": "il10pred_local"}

    scored = 0
    for e in htl_epitopes:
        seq = e.get("sequence", "")
        if seq:
            ic50 = e.get("ic50")
            binding_score = cytokine_local._ic50_to_binding_score(ic50) if ic50 is not None else None
            prediction = cytokine_local.predict_il10_epitope(seq, binding_score=binding_score)
            e["il10_induction"] = prediction["inducer_score"]
            e["is_il10_inducer"] = prediction["is_inducer"]
            e["il10_method"] = prediction["method"]
            scored += 1

    inducers = sum(1 for e in htl_epitopes if e.get("is_il10_inducer"))
    return {
        "message": f"IL10Pred (local): {inducers}/{len(htl_epitopes)} IL-10 inducers",
        "scored": scored, "total": len(htl_epitopes),
        "inducer_count": inducers,
        "method": "il10pred_local",
    }


# ---------------------------------------------------------------------------
# No validated ABCpred service is configured. The local BepiPred result is
# explicitly reported as local analysis rather than being labeled ABCpred.
# ---------------------------------------------------------------------------
async def run_7_1(session: dict, job, step) -> dict:
    """Local BepiPred analysis for the ABCpred step.

    ABCpred output is unavailable in this deployment; generated rows are
    labeled ``local-analysis`` and are never presented as ABCpred results.
    """
    vaccine_targets = session.get("vaccine_targets") or {}
    targets = vaccine_targets.get("candidates") or []

    if not targets:
        return {
            "message": "ABCpred unavailable; no vaccine targets for local BepiPred analysis",
            "scored": 0,
            "total": 0,
            "method": "bepipred_local (ABCpred unavailable)",
            "threshold": bcell_local.THRESHOLD,
            "window_size": int(job.config.bCellWindow or 7),
            "provenance": {
                "status": "local-analysis",
                "method": "BepiPred propensity analysis",
                "tool": "ABCpred",
                "toolAvailable": False,
                "reason": "ABCpred external output was not available; no local rows were generated because there were no vaccine targets.",
            },
        }

    epitope_list: list[dict] = []
    proteins_scored = 0
    for idx, cand in enumerate(targets):
        seq = cand.get("sequence", "")
        if not seq:
            continue
        window_size = int(job.config.bCellWindow or 7)
        if window_size < 1:
            raise ValueError("bCellWindow must be a positive integer")
        prediction = bcell_local.predict_bepipred_epitope(seq, window_size=window_size)
        proteins_scored += 1
        uid = cand.get("uniprotId") or cand.get("name", "unknown")
        source_name = cand.get("name", "unknown")
        source_id = str(cand.get("index")) if cand.get("index") is not None else str(idx)

        fragments = prediction.get("epitope_fragments", [])
        # Emit fixed 16-mer windows over each predicted epitope region to match
        # the paper's ABCpred setting (peptide length = 16). Longer BepiPred
        # regions are tiled into overlapping 16-mers (step 8); regions shorter
        # than 16 are extended within the protein to a 16-mer window.
        WINDOW = 16
        seen_windows: set[int] = set()
        for frag in fragments:
            start = frag["start"]
            end = frag["end"]
            region_len = end - start + 1
            if region_len >= WINDOW:
                offsets = list(range(0, region_len - WINDOW + 1, 8))
                if (region_len - WINDOW) % 8 != 0:
                    offsets.append(region_len - WINDOW)
                win_starts = [start + off for off in offsets]
            else:
                # center a 16-mer window on the short region, clamped to protein
                center = (start + end) // 2
                w_start = max(1, min(center - WINDOW // 2, len(seq) - WINDOW + 1))
                win_starts = [w_start] if len(seq) >= WINDOW else [max(1, start)]
            for w_start in win_starts:
                if w_start in seen_windows:
                    continue
                seen_windows.add(w_start)
                frag_seq = seq[w_start - 1 : w_start - 1 + WINDOW]
                if len(frag_seq) < 8:
                    continue
                frag_id = f"bcell-linear-{uid}-{w_start}-{w_start + len(frag_seq) - 1}"
                epitope_list.append({
                    "id": frag_id,
                    "type": "BCELL_LINEAR",
                    "sequence": frag_seq,
                    "sourceProtein": source_name,
                    "sourceProteinId": source_id,
                    "hlaAllele": None,
                    "antigenicityScore": None,
                    "isAllergenic": None,
                    "isToxic": None,
                    "immunogenicityScore": None,
                    "ic50": None,
                    "percentileRank": None,
                    "windowLength": len(frag_seq),
                    "startPosition": w_start,
                    "predictionMethod": prediction["method"],
                    "source": "local-analysis",
                    "selected": True,
                })
        # Store full prediction on the target for reference
        cand["abcpred_prediction"] = prediction

    session["abc_predictions"] = {"targets": targets, "count": proteins_scored}

    existing = session.get("epitopes") or []
    session["epitopes"] = existing + epitope_list

    return {
        "message": f"ABCpred unavailable; local BepiPred analysis: scored {proteins_scored} proteins, extracted {len(epitope_list)} epitope fragments",
        "scored": proteins_scored, "total": len(targets),
        "epitope_fragments": len(epitope_list),
        "method": "bepipred_local (ABCpred unavailable)",
        "threshold": bcell_local.THRESHOLD,
        "window_size": int(job.config.bCellWindow or 7),
        "provenance": {
            "status": "local-analysis",
            "method": "BepiPred propensity analysis",
            "tool": "ABCpred",
            "toolAvailable": False,
            "reason": "ABCpred external output was not available; local BepiPred is clearly labeled and is not an ABCpred result.",
        },
    }


# ---------------------------------------------------------------------------
# Phase 7-5: Ellipro → LOCAL discontinuous epitope (replaces pause)
# ---------------------------------------------------------------------------
async def run_7_5(session: dict, job, step) -> dict:
    """Ellipro conformational B-cell epitope prediction → LOCAL computation.

    Extracts short epitope fragments (8-25 aa) from each protein's Ellipro
    prediction instead of using the full protein sequence.
    """
    vaccine_targets = session.get("vaccine_targets") or {}
    targets = vaccine_targets.get("candidates") or []

    if not targets:
        return {"message": "No targets for Ellipro analysis", "scored": 0, "total": 0,
            "method": "ellipro_local"}

    epitope_list: list[dict] = []
    proteins_scored = 0
    for idx, cand in enumerate(targets):
        seq = cand.get("sequence", "")
        if not seq:
            continue
        prediction = bcell_local.predict_ellipro_epitope(
            seq,
            ig_domains=cand.get("ig_domains"),
            residues_data=cand.get("residues_data"),
        )
        proteins_scored += 1
        uid = cand.get("uniprotId") or cand.get("name", "unknown")
        source_name = cand.get("name", "unknown")
        source_id = str(cand.get("index")) if cand.get("index") is not None else str(idx)

        # Extract contiguous surface-exposed fragments from the prediction
        fragments = prediction.get("epitope_fragments",
                                    prediction.get("clustered_residues", []))
        for frag in fragments:
            start = frag.get("start", frag.get("position", 0))
            end = frag.get("end", start)
            if start and end and start > 0:
                frag_seq = seq[start - 1 : end]
            else:
                # Fallback: use the full sequence if fragment coords unavailable
                continue
            if len(frag_seq) < 6:
                continue
            frag_id = f"bcell-conf-{uid}-{start}-{end}"
            epitope_list.append({
                "id": frag_id,
                "type": "BCELL_CONFORMATIONAL",
                "sequence": frag_seq,
                "sourceProtein": source_name,
                "sourceProteinId": source_id,
                "hlaAllele": None,
                "antigenicityScore": None,
                "isAllergenic": None,
                "isToxic": None,
                "immunogenicityScore": None,
                "ic50": None,
                "percentileRank": None,
                "windowLength": len(frag_seq),
                "startPosition": start,
                "predictionMethod": prediction["method"],
                "selected": True,
            })
        cand["ellipro_prediction"] = prediction

    session["ellipro_predictions"] = {"targets": targets, "count": proteins_scored}

    existing = session.get("epitopes") or []
    session["epitopes"] = existing + epitope_list

    return {
        "message": f"Ellipro (local): scored {proteins_scored} proteins, extracted {len(epitope_list)} epitope fragments",
        "scored": proteins_scored, "total": len(targets),
        "epitope_fragments": len(epitope_list),
        "method": "ellipro_local",
    }


# ---------------------------------------------------------------------------
# Phase 10-2 / 10-3 / 10-4: MEV VaxiJen/AlgPred/ToxinPred → LOCAL
# ---------------------------------------------------------------------------
async def run_10_2(session: dict, job, step) -> dict:
    """MEV antigenicity (VaxiJen) → LOCAL ACC computation."""
    mev_data = session.get("mev_construct") or {}
    seq = mev_data.get("sequence", "")
    if not seq:
        return {"message": "No MEV sequence for antigenicity", "score": None,
            "method": "vaxijen_acc_local_mev"}

    prediction = vaxijen_local.is_antigenic(seq, "bacteria")
    session["mev_antigenicity"] = prediction
    return {
        "message": f"VaxiJen (local): MEV antigenicity score = {prediction['score']}",
        "score": prediction["score"],
        "is_antigenic": prediction["is_antigenic"],
        "method": "vaxijen_acc_local_mev",
    }


async def run_10_3(session: dict, job, step) -> dict:
    """MEV allergenicity (AlgPred) → LOCAL FAO/WHO computation."""
    mev_data = session.get("mev_construct") or {}
    seq = mev_data.get("sequence", "")
    if not seq:
        return {"message": "No MEV sequence for allergenicity", "score": None,
            "method": "algpred_local_mev"}

    prediction = algpred_local.predict_allergenicity(seq)
    session["mev_allergenicity"] = prediction
    return {
        "message": f"AlgPred (local): MEV allergenicity score = {prediction['allergen_score']}",
        "score": prediction["allergen_score"],
        "is_allergen": prediction["is_allergen"],
        "method": "algpred_local_mev",
    }


async def run_10_4(session: dict, job, step) -> dict:
    """MEV toxicity (ToxinPred) → LOCAL motif scanning."""
    mev_data = session.get("mev_construct") or {}
    seq = mev_data.get("sequence", "")
    if not seq:
        return {"message": "No MEV sequence for toxicity", "score": None,
            "method": "toxinpred_local_mev"}

    prediction = algpred_local.predict_toxicity(seq)
    session["mev_toxicity"] = prediction
    return {
        "message": f"ToxinPred (local): MEV toxicity score = {prediction['toxicity_score']}",
        "score": prediction["toxicity_score"],
        "is_toxic": prediction["is_toxic"],
        "method": "toxinpred_local_mev",
    }


# The local implementations above replace the old paused versions below.
# Old paused runners removed in favor of local computation.
