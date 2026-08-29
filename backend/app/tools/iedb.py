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
from dataclasses import asdict, dataclass
from typing import Any, Iterable

import httpx

from app.tools import api_cache

MHCI_URL = "https://tools-cluster-interface.iedb.org/tools_api/mhci/"
MHCII_URL = "https://tools-cluster-interface.iedb.org/tools_api/mhcii/"
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
REQUEST_TIMEOUT = 180.0
MAX_RETRIES = 4
# IEDB pairs allele/length lists element-wise, so each allele needs a length.
DEFAULT_MHCI_LENGTHS = ("9", "10")
DEFAULT_MHCII_LENGTHS = ("15",)
# IEDB's live consensus MHC-I endpoint supports 12-mers (verified by a real
# request on the configured endpoint). The paper protocol therefore uses the
# consensus 12-mer path here; no local predictor is substituted on outage.
CTL_LENGTHS = ("12",)
CTL_METHOD = "consensus"
# Strong-binder defaults used by generic helper callers.
MHCI_STRONG_PERCENTILE = 2.0
MHCII_STRONG_PERCENTILE = 2.0

# IEDB batches sequences by URL-encoding multi-FASTA; a polite client keeps
# each request small (a handful of full-length proteins) and spaces them out.
# The cluster interface rate-limits bursts by returning HTTP 500, so we wait
# well between attempts.
BATCH_SIZE = 4
BATCH_COOLDOWN_SEC = 3.0
ALLELE_BATCH_SIZE = 5

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
    # Rank column name varies by method:
    #   netmhcpan_el (MHC-I)      -> percentile_rank
    #   consensus (MHC-I)         -> consensus_percentile_rank
    #   consensus (MHC-II)        -> percentile_rank
    #   netmhciipan_el (MHC-II)   -> rank
    _RANK_KEYS = ("consensus_percentile_rank", "percentile_rank", "rank", "adjusted_rank")
    for raw in reader:
        try:
            percentile = None
            for _k in _RANK_KEYS:
                _v = raw.get(_k)
                if _v not in (None, "", "-"):
                    try:
                        percentile = float(_v)
                        break
                    except ValueError:
                        continue
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
    """Predict MHC-I (CTL) epitopes using small, cacheable allele batches.

    IEDB's shared cluster is unreliable when all population alleles are sent
    in one request. Splitting the allele panel keeps each request within the
    service's practical limit while preserving the complete consensus panel.
    """
    all_predictions: list[EpitopePrediction] = []
    for start in range(0, len(alleles), ALLELE_BATCH_SIZE):
        batch_alleles = alleles[start : start + ALLELE_BATCH_SIZE]
        all_predictions.extend(
            await _predict(
                MHCI_URL,
                queries,
                alleles=batch_alleles,
                lengths=lengths,
                method=method,
                has_rank=False,
            )
        )
    return all_predictions


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
    all_predictions: list[EpitopePrediction] = []
    for start in range(0, len(alleles), ALLELE_BATCH_SIZE):
        batch_alleles = alleles[start : start + ALLELE_BATCH_SIZE]
        preds = await _predict(MHCII_URL, queries, alleles=batch_alleles, lengths=lengths, method=method, has_rank=True)
        all_predictions.extend(preds)
    return all_predictions


def _serialize_prediction(prediction: EpitopePrediction) -> dict[str, Any]:
    """Convert a parsed IEDB row into the JSON-safe cache representation."""
    return asdict(prediction)


def _deserialize_prediction(value: dict[str, Any]) -> EpitopePrediction:
    """Restore one :class:`EpitopePrediction` from the cache representation."""
    return EpitopePrediction(
        allele=str(value.get("allele") or ""),
        seq_num=int(value.get("seq_num") or 0),
        start=int(value.get("start") or 0),
        end=int(value.get("end") or 0),
        length=int(value.get("length") or 0),
        peptide=str(value.get("peptide") or ""),
        ic50=float(value["ic50"]) if value.get("ic50") is not None else None,
        percentile_rank=(
            float(value["percentile_rank"])
            if value.get("percentile_rank") is not None
            else None
        ),
        score=float(value["score"]) if value.get("score") is not None else None,
        core=str(value["core"]) if value.get("core") is not None else None,
    )


def _cache_params(
    url: str,
    queries: list[tuple[str, str]],
    *,
    alleles: list[str],
    lengths: tuple[str, ...],
    method: str,
    has_rank: bool,
) -> dict[str, Any]:
    """Build a collision-safe key for one complete IEDB prediction request.

    Query names are retained alongside their sequences because the parsed rows
    use positional ``seq_num`` values.  This prevents a cached result from a
    differently labelled query set from being applied to a caller's source
    proteins, while still making every sequence value explicit in the key.
    """
    return {
        "cacheContract": "iedb-v2",
        "url": url,
        "method": method,
        "alleles": list(alleles),
        "lengths": list(lengths),
        "sequences": [{"name": name, "sequence": sequence} for name, sequence in queries],
        "has_rank": has_rank,
    }


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
    params = _cache_params(
        url,
        queries,
        alleles=alleles,
        lengths=lengths,
        method=method,
        has_rank=has_rank,
    )

    async def fetch_predictions() -> list[dict[str, Any]]:
        """Fetch, parse, and globally remap one complete IEDB request."""
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
                        "species": "human",
                    },
                )
                # IEDB numbers `seq_num` 1..N within each request only. Remap
                # to the GLOBAL query index so callers can attribute every
                # epitope row back to its source protein regardless of batching.
                for prediction in _parse_tsv(response.text, has_rank=has_rank):
                    if 1 <= prediction.seq_num <= len(batch):
                        prediction.seq_num = i + prediction.seq_num
                    predictions.append(prediction)
                if i + BATCH_SIZE < len(queries):
                    await asyncio.sleep(BATCH_COOLDOWN_SEC)
            return [_serialize_prediction(prediction) for prediction in predictions]

    cached_rows = await api_cache.cached_async_api_call("iedb", params, fetch_predictions)
    return [_deserialize_prediction(row) for row in cached_rows]


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
