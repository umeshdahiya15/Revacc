"""Runner registry — maps pipeline step ids to real tool callbacks.

When a step has a registered runner AND the job has `config.realTools`
enabled, the simulator executes the tool instead of faking the result.
Runners return a JSON-safe dict (counts, flags, …) that becomes the step's
`result` and the summary of its `step_completed` event.
"""
from __future__ import annotations

import asyncio
import os
import time
from typing import Awaitable, Callable

from ..models import Job, Step
from . import cdhit, deg, iedb, ncbiblast, uniprot
from . import blastdb_local
from .graceful_pause import ToolUnavailableError
from .runner_additions import (
    run_2_2, run_2_3, run_2_4,
    run_3_1, run_3_2, run_3_3, run_3_4,
    run_4_1, run_4_2_structure, run_4_3_coordinate_analysis, run_4_4,
    run_5_2, run_5_3, run_5_4, run_5_5,
    run_6_2, run_6_3, run_6_4, run_6_5, run_6_6, run_6_7,
    run_7_1, run_7_2, run_7_3, run_7_4, run_7_5,
    run_8_1, run_8_2,
    run_9_1, run_9_1_adj,
    run_10_1, run_10_2, run_10_3, run_10_4, run_10_5,
    run_11_1, run_11_2, run_11_3, run_11_4, run_11_5,
    run_12_1, run_13_1, run_13_2, run_13_3,
    run_14_1,
)


StepRunner = Callable[[Job, Step], Awaitable[dict]]

# Essentiality is determined by BLASTp against the COMPLETE DEG 10 bacterial
# essential-protein set (26,619 proteins). Following the reverse-vaccinology
# literature (and DEG's own BLAST server), a protein is called essential on a
# significant e-value hit; the identity floor is kept low (20%) rather than the
# earlier over-aggressive 40% gate, which alone suppressed the count by ~2.6x.
# A real-data calibration on the captured S. agalactiae run produced 1,334
# essential representatives. The result is intentionally not rounded or
# substituted with the paper's 1,336; release/proteome/reference differences
# remain observable in the returned count.
DEG_IDENTITY_THRESHOLD = float(os.environ.get("DEG_IDENTITY_THRESHOLD", "20"))
DEG_EVALUE_THRESHOLD = float(os.environ.get("DEG_EVALUE_THRESHOLD", "1e-5"))
DEG_SCOPE = os.environ.get("DEG_SCOPE", "all").lower()

STEP_RUNNERS: dict[str, StepRunner] = {}

# Per-job transient payloads (e.g. full proteome FASTA) that must not be
# serialised into the job record served over the API.
_RUN_SESSION: dict[str, dict] = {}


def get_session(job_id: str) -> dict:
    return _RUN_SESSION.setdefault(job_id, {})


def get_session_peek(job_id: str) -> dict | None:
    return _RUN_SESSION.get(job_id)


def clear_session(job_id: str) -> None:
    _RUN_SESSION.pop(job_id, None)


def prune_session(job_id: str, keep: tuple[str, ...]) -> None:
    """Drop per-job session payloads except the listed keys.

    Used after Phase 2-1 to release the big proteome/cluster FASTA while
    keeping the essential candidates (sequences) needed by the epitope
    runners in Phases 5-6.
    """
    session = _RUN_SESSION.get(job_id)
    if session is not None:
        for key in [k for k in session if k not in keep]:
            session.pop(key, None)


def runner(step_id: str) -> Callable[[StepRunner], StepRunner]:
    def register(fn: StepRunner) -> StepRunner:
        STEP_RUNNERS[step_id] = fn
        return fn

    return register


async def run_runner(job: Job, step: Step, timeout: float = 120.0) -> dict:
    task = STEP_RUNNERS[step.id]
    return await asyncio.wait_for(task(job, step), timeout=timeout)


# Remote BLAST and IEDB are slow to poll, so those runners get a generous budget.
# Local computation runners (VaxiJen, AlgPred, ToxinPred, etc.) are fast (< 60s).
STEP_TIMEOUTS: dict[str, float] = {
    # UniProt's exact reference-proteome stream allows 300s per request and
    # may retry transient 5xx responses; do not let the generic 120s runner
    # timeout convert a slow but valid 2,105-record fetch into a false pause.
    "1-1": 600.0,
    "1-2": 600.0,   # CD-HIT (pure-python, large proteomes need more time)
    "2-1": 900.0,
    "5-1": 9000.0,  # IEDB MHC-I over all essential candidates (~318), batched
    "6-1": 9000.0,  # IEDB MHC-II over all essential candidates (~318), batched
    "2-2": 21600.0, # PSORTb 3.0 (real, Docker) over all essential candidates
    "2-3": 60.0,    # DeepTMHMM (local)
    "2-4": 5400.0,  # Phobius (EBI REST, one protein at a time, ~13s/candidate)
    "3-1": 60.0,    # AlgPred (local)
    "3-2": 60.0,    # VaxiJen (local)
    "3-3": 600.0,   # VFDB BLAST
    "3-4": 1200.0,  # Human Homology BLAST
    "4-1": 60.0,    # ProtParam (BioPython)
    "4-2": 600.0,    # configured real structure provider (AlphaFold DB by default)
    "4-3": 120.0,   # local analysis of real AlphaFold DB PDB coordinates
    "4-4": 60.0,    # Secondary Structure (local Chou-Fasman)
    "5-2": 30.0,    # VaxiJen CTL (local)
    "5-3": 30.0,    # AlgPred CTL (local)
    "5-4": 30.0,    # ToxinPred CTL (local)
    "5-5": 60.0,    # Immunogenicity (computation)
    "6-2": 30.0,    # IFNepitope (local)
    "6-3": 30.0,    # IL4Pred (local)
    "6-4": 30.0,    # IL10Pred (local)
    "6-5": 30.0,    # VaxiJen HTL (local)
    "6-6": 30.0,    # AlgPred HTL (local)
    "6-7": 30.0,    # ToxinPred HTL (local)
    "7-1": 60.0,    # ABCpred (local BepiPred)
    "7-2": 30.0,    # VaxiJen B-cell (local)
    "7-3": 30.0,    # AlgPred B-cell (local)
    "7-4": 30.0,    # ToxinPred B-cell (local)
    "7-5": 60.0,    # Ellipro (local)
    "8-1": 120.0,   # Population Coverage (IEDB-AR)
    "8-2": 60.0,    # Epitope Overlap (computation)
    "9-1": 30.0,    # Adjuvant Selection (local)
    "9-2": 60.0,    # MEV Assembly
    "10-1": 60.0,   # ProtParam (MEV construct)
    "10-2": 30.0,   # VaxiJen MEV (local)
    "10-3": 30.0,   # AlgPred MEV (local)
    "10-4": 30.0,   # ToxinPred MEV (local)
    "10-5": 60.0,   # Protein-Sol (local)
    "11-1": 600.0,  # AlphaFold DB / configured structure provider (real API)
    "11-2": 600.0,  # AlphaFold DB (real API)
    "11-3": 120.0,  # Ramachandran (BioPython)
    "11-4": 30.0,   # ERRAT (local)
    "11-5": 30.0,   # ProSA (local)
    "12-1": 300.0,  # JCat (real API)
    "13-1": 300.0,  # JCat (real API)
    "13-2": 60.0,   # Restriction Analysis (BioPython)
    "13-3": 60.0,   # In Silico Cloning (local)
    "14-1": 120.0,  # C-ImmSim (local ODE)
}


def runner_timeout(step_id: str) -> float:
    return STEP_TIMEOUTS.get(step_id, 120.0)


def _with_session(fn):
    """Adapt a ``(session, job, step)`` runner to the engine's ``(job, step)`` call."""

    async def wrapped(job: Job, step: Step) -> dict:
        return await fn(get_session(job.id), job, step)

    return wrapped


# Phase 2-2 .. 3-4 runners (correct step IDs matching pipeline.py)
for _step_id, _fn in {
    "2-2": run_2_2,
    "2-3": run_2_3,
    "2-4": run_2_4,
    "3-1": run_3_1,
    "3-2": run_3_2,
    "3-3": run_3_3,
    "3-4": run_3_4,
}.items():
    STEP_RUNNERS[_step_id] = _with_session(_fn)

# Phase 4: Structural Prediction & Validation (pipeline.py: 4-1 through 4-4)
for _step_id, _fn in {
    "4-1": run_4_1,        # ProtParam (individual proteins)
    "4-2": run_4_2_structure,  # configured real provider; AlphaFold DB alternate by default
    "4-3": run_4_3_coordinate_analysis,  # local analysis of real AlphaFold DB coordinates
    "4-4": run_4_4,        # Secondary Structure (local Chou-Fasman)
}.items():
    STEP_RUNNERS[_step_id] = _with_session(_fn)

# Phase 5: CTL Epitope Prediction & Filtering (5-2 through 5-5)
# 5-1 is IEDB MHC-I (registered via @runner decorator above)
for _step_id, _fn in {
    "5-2": run_5_2,    # VaxiJen CTL (pause)
    "5-3": run_5_3,    # AlgPred CTL (pause)
    "5-4": run_5_4,    # ToxinPred CTL (pause)
    "5-5": run_5_5,    # CTL Immunogenicity
}.items():
    STEP_RUNNERS[_step_id] = _with_session(_fn)

# Phase 6: HTL Epitope Prediction & Filtering (6-2 through 6-7)
# 6-1 is IEDB MHC-II (registered via @runner decorator above)
for _step_id, _fn in {
    "6-2": run_6_2,  # IFNepitope (pause)
    "6-3": run_6_3,  # IL4Pred (pause)
    "6-4": run_6_4,  # IL10Pred (pause)
    "6-5": run_6_5,  # VaxiJen HTL (pause)
    "6-6": run_6_6,  # AlgPred HTL (pause)
    "6-7": run_6_7,  # ToxinPred HTL (pause)
}.items():
    STEP_RUNNERS[_step_id] = _with_session(_fn)

# Phase 7: B-Cell Epitope Prediction & Filtering (7-1 through 7-5)
for _step_id, _fn in {
    "7-1": run_7_1,  # ABCpred (pause)
    "7-2": run_7_2,  # VaxiJen B-cell (pause)
    "7-3": run_7_3,  # AlgPred B-cell (pause)
    "7-4": run_7_4,  # ToxinPred B-cell (pause)
    "7-5": run_7_5,  # Ellipro (pause)
}.items():
    STEP_RUNNERS[_step_id] = _with_session(_fn)

# Phase 8: Population Coverage & Epitope Overlap (8-1, 8-2)
for _step_id, _fn in {
    "8-1": run_8_1,  # Population Coverage (IEDB-AR)
    "8-2": run_8_2,  # Epitope Overlap (BT Overlap)
}.items():
    STEP_RUNNERS[_step_id] = _with_session(_fn)

# Phase 9: MEV Construct Assembly (9-1 adjuvant selection, 9-2 assembly)
for _step_id, _fn in {
    "9-1": run_9_1_adj,  # Adjuvant Selection (pause)
    "9-2": run_9_1,      # MEV Assembly
}.items():
    STEP_RUNNERS[_step_id] = _with_session(_fn)

# Phase 10: MEV Construct Validation (10-1 through 10-5)
for _step_id, _fn in {
    "10-1": run_10_1,  # ProtParam (MEV construct)
    "10-2": run_10_2,  # VaxiJen MEV (pause)
    "10-3": run_10_3,  # AlgPred MEV (pause)
    "10-4": run_10_4,  # ToxinPred MEV (pause)
    "10-5": run_10_5,  # Protein-Sol (pause)
}.items():
    STEP_RUNNERS[_step_id] = _with_session(_fn)

# Phase 11: MEV 3D Structure & Validation (11-1 through 11-5)
for _step_id, _fn in {
    "11-1": run_4_4,      # Secondary Structure (SOPMA→local Chou-Fasman)
    "11-2": run_11_2,     # AlphaFold DB / validated external model
    "11-3": run_11_3,     # Ramachandran (local measured coordinates)
    "11-4": run_11_4,     # ERRAT-like local measured-coordinate analysis
    "11-5": run_11_5,     # ProSA-like local measured-coordinate analysis
}.items():
    STEP_RUNNERS[_step_id] = _with_session(_fn)

# Phase 12: Disulfide Bond Engineering (12-1: DbD2 → local)
for _step_id, _fn in {
    "12-1": run_13_1,  # DbD2 (local)
}.items():
    STEP_RUNNERS[_step_id] = _with_session(_fn)

# Phase 13: Codon Optimization & Cloning (13-1: JCat, 13-2: Restriction, 13-3: Cloning)
for _step_id, _fn in {
    "13-1": run_12_1,  # JCat
    "13-2": run_13_2,  # Restriction Analysis
    "13-3": run_13_3,  # In Silico Cloning (local)
}.items():
    STEP_RUNNERS[_step_id] = _with_session(_fn)

# Phase 14: Immune Simulation (14-1: C-ImmSim)
for _step_id, _fn in {
    "14-1": run_14_1,  # C-ImmSim (pause)
}.items():
    STEP_RUNNERS[_step_id] = _with_session(_fn)


@runner("1-1")
async def runner_retrieve_proteome(job: Job, step: Step) -> dict:
    started = time.monotonic()

    # FASTA-upload source: use the file contents stored by the upload
    # endpoint (routes.py POST /api/jobs/{id}/fasta) when present.
    session = get_session(job.id)
    fasta_text = session.get("fasta_text")
    if fasta_text:
        # User-provided FASTA is not a UniProt reference-proteome response.
        # Preserve its record order and duplicate/arbitrary headers exactly.
        records = uniprot.parse_fasta(fasta_text, deduplicate=False)
        if not records:
            raise RuntimeError("Uploaded FASTA contained no parseable protein sequences.")
        session["proteins"] = records
        return {
            "source": "uploaded FASTA",
            "sourceType": "user-provided",
            "provenance": {
                "source": "user-uploaded FASTA",
                "cacheType": "real",
                "fileName": job.config.fastaFileName or "user upload",
            },
            "taxonId": job.config.taxonId,
            **uniprot.stats(records),
            "duration": round(time.monotonic() - started, 1),
        }

    taxon = job.config.taxonId
    if not taxon:
        raise RuntimeError(
            "Job has no taxonId and no uploaded FASTA — cannot fetch a proteome."
        )
    requested_reviewed_only = bool(getattr(job.config, "reviewedOnly", False))
    # A pipeline proteome is always the complete UniProt reference stream;
    # legacy reviewedOnly payloads cannot narrow the scientific input silently.
    records = await uniprot.fetch_proteome(taxon, reviewed_only=False)
    session["proteins"] = records
    reviewed_only = False
    provenance = uniprot.fetch_metadata(taxon, reviewed_only=reviewed_only) or {
        "source": "https://rest.uniprot.org/uniprotkb/stream",
        "cacheType": "real",
        "cacheState": "fresh-real",
        "query": f"proteome resolved by taxon {taxon}",
    }
    provenance = {
        **provenance,
        "status": provenance.get("cacheType", "real"),
        "tool": "UniProt reference proteome",
        "taxonId": taxon,
        "proteomeId": (provenance.get("proteome") or {}).get("id"),
        "returnedRecordCount": provenance.get("returnedRecordCount", len(records)),
        "referenceProteomeProteinCount": provenance.get("referenceProteomeProteinCount"),
        "cacheState": provenance.get("cacheState", provenance.get("cacheType", "real")),
    }
    metadata = {
        "taxonId": taxon,
        "reviewedOnly": reviewed_only,
        "requestedReviewedOnly": requested_reviewed_only,
        "query": provenance.get("query"),
        "format": provenance.get("format"),
        "release": provenance.get("release"),
        "metadataFingerprint": provenance.get("metadataFingerprint"),
        "proteomeId": provenance.get("proteomeId"),
        "referenceProteomeProteinCount": provenance.get("referenceProteomeProteinCount"),
        "returnedRecordCount": provenance.get("returnedRecordCount", len(records)),
        "fastaHeaderCount": provenance.get("fastaHeaderCount"),
        "duplicateAccessionsRemoved": provenance.get("duplicateAccessionsRemoved"),
        "countMatchesProteomeMetadata": provenance.get("countMatchesProteomeMetadata"),
        "cacheState": provenance.get("cacheState"),
    }
    return {
        "source": provenance.get("source", "https://rest.uniprot.org/uniprotkb/stream"),
        "sourceType": provenance.get("status", "real"),
        "cacheType": provenance.get("cacheType", provenance.get("status", "real")),
        "cacheState": provenance.get("cacheState"),
        "proteomeId": provenance.get("proteomeId"),
        "query": provenance.get("query"),
        "referenceProteomeProteinCount": provenance.get("referenceProteomeProteinCount"),
        "returnedRecordCount": provenance.get("returnedRecordCount", len(records)),
        "provenance": provenance,
        "queryMetadata": metadata,
        "taxonId": taxon,
        "reviewedOnly": reviewed_only,
        "requestedReviewedOnly": requested_reviewed_only,
        **uniprot.stats(records),
        "duration": round(time.monotonic() - started, 1),
    }


@runner("1-2")
async def runner_remove_redundants(job: Job, step: Step) -> dict:
    session = get_session_peek(job.id) or {}
    records = session.get("proteins")
    if not records:
        raise RuntimeError(
            "No exact UniProt/uploaded proteome is present in the run session; "
            "refusing to refetch or substitute a different proteome."
        )
    sequences = cdhit.sorted_sequences(records)
    if not sequences:
        raise RuntimeError("Proteome fetch returned zero sequences.")
    threshold = float(job.config.cdHitThreshold or 0.8)
    started = time.monotonic()
    clusters, reps = await asyncio.get_running_loop().run_in_executor(
        None, lambda: cdhit.cluster(sequences, identity=threshold)
    )
    rep_sequences = [sequences[i] for i in reps]
    session = get_session(job.id)
    session["clusters"] = {
        "indices": clusters,
        "representatives": rep_sequences,
        "representativeIndices": reps,
        "representativeRecords": [records[i] for i in reps],
        "metadata": [{"index": i, "length": len(rep_sequences[i]), "members": len(clusters[i])} for i in range(len(clusters))],
    }
    # Stash the count so _update_funnel can read it after session pruning.
    fc = session.setdefault("_funnel_counts", {})
    fc["proteins"] = len(records)
    # Use total cluster count — this matches the "Non-redundant" count in MEV papers.
    fc["redundant"] = len(clusters)
    return {
        "algorithm": "cd-hit (greedy k-mer / global identity)",
        "method": "CD-HIT-compatible local greedy k-mer/global identity analysis",
        "provenance": {
            "status": "local-analysis",
            "tool": "CD-HIT-compatible local implementation",
            "nativeBinaryAvailable": False,
            "criteria": f"identity >= {threshold:.2f}",
            "reason": "Native cd-hit was not configured; the repository's documented local algorithm was used.",
        },
        **cdhit.stats(sequences, clusters, threshold),
        "duration": round(time.monotonic() - started, 1),
    }


@runner("2-1")
async def runner_identify_essential(job: Job, step: Step) -> dict:
    """Classify representatives by significant hits in the configured DEG reference.

    The default paper-basis reference is the complete DEG10 bacterial FASTA;
    a species-specific annotation/database remains available via DEG_SCOPE.
    A representative is retained only when its best hit has identity at least
    ``DEG_IDENTITY_THRESHOLD`` and e-value at most ``DEG_EVALUE_THRESHOLD``.
    """
    session = get_session(job.id)
    clusters = session.get("clusters") or {}
    representatives: list[str] = clusters.get("representatives") or []
    if not representatives:
        raise RuntimeError("No non-redundant representatives available for essentiality BLAST.")

    # ``all`` is the paper-basis scope: the complete DEG10 bacterial
    # essential-protein FASTA. Species-scoped runs remain available for
    # reproducibility, but must opt in explicitly via DEG_SCOPE=species (or
    # an organism name). Previously DEG_SCOPE was read but ignored here.
    organism = deg.organism_for_scope(DEG_SCOPE, job.pathogenName)
    if organism is None:
        session["deg_scope"] = deg.DEG10_SCOPE
        await blastdb_local.ensure_deg10_db()
        database = blastdb_local.DEG10_DB_NAME
        reference_genes = blastdb_local.deg10_reference_size()
        reference_label = deg.reference_label_for_scope(DEG_SCOPE, organism)
        algorithm = "BLASTp (local) + DEG 10"
    else:
        session["deg_scope"] = organism
        database_path = await blastdb_local.ensure_deg_db(organism=organism)
        database = os.path.basename(database_path)
        reference_genes = len(await deg.fetch_essential_genes(organism=organism))
        reference_label = deg.reference_label_for_scope(DEG_SCOPE, organism)
        algorithm = "BLASTp (local) + DEG"
    started = time.monotonic()

    queries = [(f"rep{i}", seq) for i, seq in enumerate(representatives)]
    results = await blastdb_local.blastp(
        queries,
        database=database,
        expect=DEG_EVALUE_THRESHOLD,
        hitlist_size=3,
    )

    # Map results back onto representatives by query name.
    by_def = {r.query_def: r for r in results}
    essential: list[int] = []
    kept: list[bool] = []
    for idx, _ in enumerate(representatives):
        res = by_def.get(f"rep{idx}")
        if res is None or not res.hits:
            kept.append(False)
            continue
        hit = res.hits[0]
        identity_pct = (hit.identity / hit.align_length * 100) if hit.align_length else 0.0
        is_essential = (
            identity_pct >= DEG_IDENTITY_THRESHOLD
            and hit.e_value <= DEG_EVALUE_THRESHOLD
        )
        kept.append(is_essential)
        if is_essential:
            essential.append(idx)

    records = clusters.get("representativeRecords") or []
    candidates = [
        {
            "index": idx,
            "uniprotId": (records[idx].uniprot_id if idx < len(records) else f"rep{idx}"),
            "name": (records[idx].name if idx < len(records) else f"Rep {idx}"),
            "sequence": representatives[idx],
        }
        for idx in essential
    ]

    get_session(job.id)["essential"] = {
        "indices": essential,
        "candidates": candidates,
        "representativeHits": len([r for r in results if r.hits]),
        "reference": reference_label,
        "referenceGenes": reference_genes,
    }
    # Stash essential count for _update_funnel after session pruning.
    fc = session.setdefault("_funnel_counts", {})
    fc["essential"] = len(essential)
    return {
        "algorithm": algorithm,
        "reference": reference_label,
        "referenceGenes": reference_genes,
        "sequences": len(representatives),
        "essential": len(essential),
        "removedAsNonEssential": len(representatives) - len(essential),
        "identityThreshold": f"≥ {DEG_IDENTITY_THRESHOLD:g}% identity, e ≤ {DEG_EVALUE_THRESHOLD:g}",
        **ncbiblast.stats(results),
        "duration": round(time.monotonic() - started, 1),
    }


def _epitope_candidates(job: Job) -> list[dict]:
    """Protein candidates kept in the run session, most-filtered first.

    Preference order follows the subtractive-proteomics funnel so epitopes
    are only predicted on proteins that survived every safety filter that
    actually ran: vaccine_targets (3-4) → antigenic (3-2) →
    non_allergenic (3-1) → virulence_factors (3-3) →
    surface_exposed (2-2/2-4) → essential (2-1).
    """
    session = get_session_peek(job.id) or {}
    for key in (
        "vaccine_targets",
        "antigenic",
        "non_allergenic",
        "virulence_factors",
        "surface_exposed",
        "essential",
    ):
        candidates = (session.get(key) or {}).get("candidates") or []
        if candidates:
            return candidates
    raise RuntimeError(
        "No protein candidates available — run 'Identify Essential Proteins' first."
    )


def _append_epitopes(job_id: str, epitopes: list[dict]) -> None:
    """Append epitopes to the session pool, deduplicating by id.

    Guards against duplicate rows if a step re-executes after an engine
    restart mid-tool-call.
    """
    session = get_session(job_id)
    existing = session.get("epitopes") or []
    seen = {e.get("id") for e in existing}
    for e in epitopes:
        if e.get("id") not in seen:
            seen.add(e["id"])
            existing.append(e)
    session["epitopes"] = existing


CTL_SELECT_CAP = 30   # max CTL epitopes carried into the construct
HTL_SELECT_CAP = 20   # max HTL epitopes carried into the construct


def _allele_freq_bonus(allele: str) -> float:
    """Return a small bonus (0-5) for alleles with high population frequency.

    Prefers alleles present in the NMDP bundle so the final construct
    achieves better population coverage.
    """
    from .population import NMDP_FREQ
    freq = NMDP_FREQ.get(allele, 0.0)
    if freq <= 0:
        return 0.0
    # log-scale bonus: freq 0.15 → ~3.5, freq 0.05 → ~2.2, freq 0.01 → ~1.0
    return min(5.0, max(0.0, 3.0 * (1.0 + __import__("math").log10(freq / 0.01))))


def _cap_by_rank(selected: list[iedb.EpitopePrediction], cap: int) -> list[iedb.EpitopePrediction]:
    """Globally keep the `cap` best epitopes, weighted by binding rank + allele frequency."""
    if len(selected) <= cap:
        return selected
    def sort_key(p):
        rank = p.percentile_rank if p.percentile_rank is not None else 999
        bonus = _allele_freq_bonus(p.allele)
        return rank - bonus
    ranked = sorted(selected, key=sort_key)
    kept = sorted(ranked[:cap], key=lambda p: (p.seq_num, p.start, p.allele))
    return kept


@runner("5-1")
async def runner_predict_ctl_epitopes(job: Job, step: Step) -> dict:
    """Predict CTL (MHC-I) epitopes for every essential candidate via IEDB.

    The best strong-binder epitope per protein/allele/length is kept; the
    full prediction table is stored on the job as `epitopes` (type CTL) so
    the frontend phase 5 table renders real results. Each selected epitope
    carries full provenance (source protein, position, allele, percentile).
    """
    candidates = _epitope_candidates(job)
    alleles = job.config.hlaMhc1 or [
        "HLA-A*02:01", "HLA-A*02:05", "HLA-A*01:01", "HLA-A*03:01",
        "HLA-A*11:01", "HLA-A*23:01", "HLA-A*24:02", "HLA-A*26:01",
        "HLA-A*30:01", "HLA-A*32:01", "HLA-A*33:01", "HLA-A*68:01",
        "HLA-B*07:02", "HLA-B*08:01", "HLA-B*15:01", "HLA-B*35:01",
        "HLA-B*40:01", "HLA-B*44:02", "HLA-B*51:01", "HLA-B*53:01",
        "HLA-B*57:01", "HLA-B*58:01",
        "HLA-C*01:02", "HLA-C*04:01", "HLA-C*07:02",
    ]
    started = time.monotonic()

    queries = [
        (f">{i}|{c['uniprotId']}|{c['name']}".replace(" ", "_"), c["sequence"])
        for i, c in enumerate(candidates)
    ]
    # CTL protocol: live IEDB consensus, 12-mer, percentile <= 2. The live
    # endpoint was probed successfully; if it becomes unavailable, the runner
    # pauses rather than substituting a non-IEDB scientific predictor.
    try:
        predictions = await iedb.predict_mhci(
            queries, alleles=alleles, lengths=iedb.CTL_LENGTHS, method=iedb.CTL_METHOD
        )
    except ConnectionError as exc:
        raise ToolUnavailableError(
            tool_name="IEDB consensus 12-mer (MHC-I)",
            reason=(
                "IEDB retries and the exact-request cache were exhausted; a local "
                "propensity heuristic is not scientifically equivalent to NetMHCpan."
            ),
            workaround="Retry IEDB when available or attach validated IEDB predictions; no synthetic epitopes were emitted.",
        ) from exc
    binders = iedb.strong_binders(
        predictions,
        mhci=True,
        threshold=job.config.mhciPercentile,
    )
    selected = _cap_by_rank(iedb.top_per_protein(binders, per_seq=5), CTL_SELECT_CAP)

    epitopes = _serialize_epitopes(
        selected, kind="CTL", candidates=candidates
    )
    _append_epitopes(job.id, epitopes)

    return {
        "algorithm": "IEDB MHC-I consensus (12-mer)",
        "proteins": len(candidates),
        "alleles": len(alleles),
        "predicted": len(predictions),
        "strongBinders": len(binders),
        "selected": len(selected),
        "selectedCap": CTL_SELECT_CAP,
        "lengths": [12],
        "method": "consensus (MHC-I 12-mer)",
        "duration": round(time.monotonic() - started, 1),
    }


@runner("6-1")
async def runner_predict_htl_epitopes(job: Job, step: Step) -> dict:
    """Predict HTL (MHC-II) epitopes for every essential candidate via IEDB."""
    candidates = _epitope_candidates(job)
    alleles = job.config.hlaMhc2 or [
        "HLA-DRB1*01:01", "HLA-DRB1*03:01", "HLA-DRB1*04:01",
        "HLA-DRB1*07:01", "HLA-DRB1*08:01", "HLA-DRB1*09:01",
        "HLA-DRB1*11:01", "HLA-DRB1*12:01", "HLA-DRB1*13:01",
        "HLA-DRB1*15:01", "HLA-DRB1*14:01", "HLA-DRB1*16:01",
        "HLA-DPB1*04:01", "HLA-DPB1*01:01", "HLA-DPB1*04:02",
        "HLA-DPB1*02:01", "HLA-DPB1*05:01", "HLA-DPB1*03:01",
        "HLA-DQB1*03:02", "HLA-DQB1*05:01", "HLA-DQB1*06:02",
        "HLA-DQB1*03:01", "HLA-DQB1*02:01", "HLA-DQB1*04:01",
        "HLA-DQB1*06:01",
    ]
    started = time.monotonic()

    queries = [
        (f">{i}|{c['uniprotId']}|{c['name']}".replace(" ", "_"), c["sequence"])
        for i, c in enumerate(candidates)
    ]
    # Paper method: consensus MHC-II binding, 15-mer epitopes, percentile <= 2.
    try:
        predictions = await iedb.predict_mhcii(
            queries, alleles=alleles, lengths=("15",), method="consensus"
        )
    except ConnectionError as exc:
        raise ToolUnavailableError(
            tool_name="IEDB consensus (MHC-II)",
            reason=(
                "IEDB retries and the exact-request cache were exhausted; a local "
                "propensity heuristic is not scientifically equivalent to MHC-II prediction."
            ),
            workaround="Retry IEDB when available or attach validated IEDB predictions; no synthetic epitopes were emitted.",
        ) from exc
    binders = iedb.strong_binders(
        predictions,
        mhci=False,
        threshold=job.config.mhciiPercentile,
    )
    selected = _cap_by_rank(iedb.top_per_protein(binders, per_seq=3), HTL_SELECT_CAP)

    epitopes = _serialize_epitopes(
        selected, kind="HTL", candidates=candidates
    )
    _append_epitopes(job.id, epitopes)

    return {
        "algorithm": "IEDB MHC-II consensus (15-mer)",
        "proteins": len(candidates),
        "alleles": len(alleles),
        "predicted": len(predictions),
        "strongBinders": len(binders),
        "selected": len(selected),
        "selectedCap": HTL_SELECT_CAP,
        "lengths": [15],
        "method": "consensus",
        "algorithmName": "IEDB MHC-II consensus (15-mer)",
        "duration": round(time.monotonic() - started, 1),
    }


def _serialize_epitopes(
    predictions: list[iedb.EpitopePrediction],
    *,
    kind: str,
    candidates: list[dict],
) -> list[dict]:
    """Convert IEDB rows into the frontend `Epitope` JSON shape.

    IEDB `seq_num` is remapped to the GLOBAL query index by `iedb._predict`,
    and queries were built one-per-candidate in order, so
    ``candidates[seq_num - 1]`` is always the exact source protein.
    """
    epitopes: list[dict] = []
    for p in predictions:
        cand = candidates[p.seq_num - 1] if 1 <= p.seq_num <= len(candidates) else {}
        uniprot_id = cand.get("uniprotId") or "unknown"
        allele_tag = (p.allele or "NA").replace("*", "").replace(":", "")
        rank = p.percentile_rank
        # A percentile rank is a relative MHC-binding measure, not an
        # immunogenicity measurement. Step 5-5 computes the explicitly local
        # immunogenicity score from the real IEDB inputs; do not expose a
        # synthetic 100-rank value as IEDB immunogenicity here.
        epitopes.append(
            {
                "id": f"{kind.lower()}-{uniprot_id}-{p.start}-{p.length}-{allele_tag}",
                "type": kind,
                "sequence": p.peptide,
                "sourceProtein": uniprot_id,
                "sourceProteinId": str(cand.get("index")) if cand.get("index") is not None else None,
                "sourceProteinName": cand.get("name"),
                "startPosition": p.start,
                "hlaAllele": p.allele,
                "immunogenicityScore": None,
                "ic50": p.ic50,
                "percentileRank": rank,
                "windowLength": p.length,
                "predictionMethod": (
                    "IEDB consensus (MHC-I 12-mer)" if kind == "CTL"
                    else "IEDB consensus (MHC-II 15-mer)"
                ),
                "source": "real",
                "selected": True,
            }
        )
    return epitopes
