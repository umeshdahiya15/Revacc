"""IEDB REST client — Phase 5 (MHC-I / CTL) & Phase 6 (MHC-II / HTL) epitopes.

Thin wrapper over the IEDB tools cluster REST interface (the same backend the
web UI uses). Sends a form-encoded POST and parses the tab-separated
prediction table back into structured records:

  MHC-I: allele  seq_num  start  end  length  peptide  ic50  percentile_rank
  MHC-II: allele  seq_num  start  end  length  core_peptide  peptide  score  rank

The REST interface was confirmed live: `tools-cluster-interface.iedb.org`
is the current host (the older `services.iedb.org` name no longer resolves),
documented at https://tools.iedb.org/main/tools-api/.
"""
from __future__ import annotations

import asyncio
import csv
import io
from dataclasses import dataclass
from typing import Iterable

import httpx

MHCI_URL = "https://tools-cluster-interface.iedb.org/tools_api/mhci/"
MHCII_URL = "https://tools-cluster-interface.iedb.org/tools_api/mhcii/"
USER_AGENT = "mev-pipeline/0.1 (multi-epitope vaccine pipeline; contact@example.org)"
REQUEST_TIMEOUT = 180.0
MAX_RETRIES = 4
# IEDB pairs allele/length lists element-wise, so each allele needs a length.
DEFAULT_MHCI_LENGTHS = ("9", "10")
DEFAULT_MHCII_LENGTHS = ("15",)
# Strong binder thresholds used when selecting epitopes for the funnel.
MHCI_STRONG_PERCENTILE = 2.0
MHCII_STRONG_PERCENTILE = 10.0

# IEDB batches sequences by URL-encoding multi-FASTA; a polite client keeps
# each request small (a handful of full-length proteins) and spaces them out.
# The cluster interface rate-limits bursts by returning HTTP 500, so we wait
# well between attempts.
BATCH_SIZE = 4
BATCH_COOLDOWN_SEC = 3.0

_RETRYABLE = (429, 500, 502, 503, 504)
# Longer backoff than a local tool: IEDB's shared cluster needs breathing room.
_RETRY_BACKOFF = (3.0, 8.0, 20.0)


@dataclass
class EpitopePrediction:
    """One predicted epitope row from the IEDB TSV."""

    allele: str
    seq_num: int
    start: int
    end: int
    length: int
    peptide: str
    ic50: float | None = None
    percentile_rank: float | None = None
    score: float | None = None
    core: str | None = None


async def _post(client: httpx.AsyncClient, url: str, data: dict[str, str]) -> httpx.Response:
    last_error: Exception | None = None
    for attempt in range(MAX_RETRIES):
        try:
            response = await client.post(url, data=data)
            if response.status_code in _RETRYABLE and attempt < MAX_RETRIES - 1:
                await asyncio.sleep(_RETRY_BACKOFF[attempt])
                continue
            response.raise_for_status()
            return response
        except httpx.HTTPError as exc:
            last_error = exc
            if attempt < MAX_RETRIES - 1:
                await asyncio.sleep(_RETRY_BACKOFF[attempt])
    raise ConnectionError(f"IEDB unreachable after {MAX_RETRIES} attempts: {last_error}")


def _fasta(queries: Iterable[tuple[str, str]]) -> str:
    return "".join(f"{name if name.startswith('>') else '>' + name}\n{seq}\n" for name, seq in queries)


def _parse_tsv(text: str, *, has_rank: bool) -> list[EpitopePrediction]:
    rows: list[EpitopePrediction] = []
    reader = csv.DictReader(io.StringIO(text), delimiter="\t")
    for raw in reader:
        rank_key = "rank" if has_rank else "percentile_rank"
        try:
            percentile = float(raw.get(rank_key) or 0) if raw.get(rank_key) else None
            ic50 = float(raw.get("ic50") or 0) if raw.get("ic50") else None
            score = float(raw.get("score") or 0) if raw.get("score") else None
            rows.append(
                EpitopePrediction(
                    allele=raw.get("allele") or "",
                    seq_num=int(raw.get("seq_num") or 0),
                    start=int(raw.get("start") or 0),
                    end=int(raw.get("end") or 0),
                    length=int(raw.get("length") or 0),
                    peptide=raw.get("peptide") or "",
                    ic50=ic50,
                    percentile_rank=percentile,
                    score=score,
                    core=raw.get("core") or raw.get("core_peptide") or None,
                )
            )
        except ValueError:
            continue  # skip malformed rows
    return rows


def _flatten_alleles(alleles: list[str], lengths: tuple[str, ...]) -> tuple[str, str]:
    """Pair alleles with lengths element-wise (IEDB requires equal counts)."""
    expanded: list[str] = []
    for allele in alleles:
        expanded.extend([allele] * len(lengths))
    return ",".join(expanded), ",".join(lengths * len(alleles))


async def predict_mhci(
    queries: list[tuple[str, str]],
    *,
    alleles: list[str],
    lengths: tuple[str, ...] = DEFAULT_MHCI_LENGTHS,
    method: str = "netmhcpan_el",
) -> list[EpitopePrediction]:
    """Predict MHC-I (CTL) epitopes for (name, sequence) protein queries."""
    return await _predict(MHCI_URL, queries, alleles=alleles, lengths=lengths, method=method, has_rank=False)


async def predict_mhcii(
    queries: list[tuple[str, str]],
    *,
    alleles: list[str],
    lengths: tuple[str, ...] = DEFAULT_MHCII_LENGTHS,
    method: str = "netmhciipan_el",
) -> list[EpitopePrediction]:
    """Predict MHC-II (HTL) epitopes for (name, sequence) protein queries.

    Splits alleles into batches of 5 to avoid IEDB API limits.
    """
    BATCH_ALLELE_SIZE = 5
    all_predictions: list[EpitopePrediction] = []
    for start in range(0, len(alleles), BATCH_ALLELE_SIZE):
        batch_alleles = alleles[start : start + BATCH_ALLELE_SIZE]
        preds = await _predict(MHCII_URL, queries, alleles=batch_alleles, lengths=lengths, method=method, has_rank=True)
        all_predictions.extend(preds)
    return all_predictions


async def _predict(
    url: str,
    queries: list[tuple[str, str]],
    *,
    alleles: list[str],
    lengths: tuple[str, ...],
    method: str,
    has_rank: bool,
) -> list[EpitopePrediction]:
    if not queries:
        return []
    if not alleles:
        raise ValueError("At least one MHC allele is required for IEDB prediction.")

    allele_csv, length_csv = _flatten_alleles(alleles, lengths)

    async with httpx.AsyncClient(
        timeout=REQUEST_TIMEOUT,
        headers={"User-Agent": USER_AGENT},
        follow_redirects=True,
    ) as client:
        predictions: list[EpitopePrediction] = []
        for i in range(0, len(queries), BATCH_SIZE):
            batch = queries[i : i + BATCH_SIZE]
            response = await _post(
                client,
                url,
                {
                    "method": method,
                    "sequence_text": _fasta(batch),
                    "allele": allele_csv,
                    "length": length_csv,
                },
            )
            # IEDB numbers `seq_num` 1..N within each request only. Remap to
            # the GLOBAL query index so callers can attribute every epitope
            # row back to its source protein regardless of batching.
            for p in _parse_tsv(response.text, has_rank=has_rank):
                if 1 <= p.seq_num <= len(batch):
                    p.seq_num = i + p.seq_num
                predictions.append(p)
            if i + BATCH_SIZE < len(queries):
                await asyncio.sleep(BATCH_COOLDOWN_SEC)
        return predictions


def strong_binders(
    predictions: list[EpitopePrediction],
    *,
    mhci: bool = True,
    threshold: float | None = None,
) -> list[EpitopePrediction]:
    """Keep only strong-binder epitopes (percentile rank below threshold)."""
    cutoff = threshold if threshold is not None else (MHCI_STRONG_PERCENTILE if mhci else MHCII_STRONG_PERCENTILE)
    return [p for p in predictions if p.percentile_rank is not None and p.percentile_rank <= cutoff]


def top_per_protein(predictions: list[EpitopePrediction], *, per_seq: int = 5) -> list[EpitopePrediction]:
    """Keep the `per_seq` lowest percentile_rank epitopes for each protein."""
    by_seq: dict[int, list[EpitopePrediction]] = {}
    for p in predictions:
        by_seq.setdefault(p.seq_num, []).append(p)
    selected: list[EpitopePrediction] = []
    for seq_num in sorted(by_seq):
        ranked = sorted(by_seq[seq_num], key=lambda p: (p.percentile_rank if p.percentile_rank is not None else 999))
        selected.extend(ranked[:per_seq])
    return selected


def stats(predictions: list[EpitopePrediction]) -> dict:
    return {
        "predicted": len(predictions),
        "alleles": sorted({p.allele for p in predictions if p.allele}),
        "lengths": sorted({p.length for p in predictions if p.length}),
    }
