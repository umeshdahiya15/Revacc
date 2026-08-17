"""DEG (Database of Essential Genes) reference client — Phase 2, step 1.

Used by "Identify Essential Proteins": the pipeline marks a candidate protein
as essential when it has a significant BLAST hit against an essential gene of
 a reference organism. This module supplies an organism-wide essential-gene
 reference set plus the mapping from each gene to its NCBI protein UID.
"""
from __future__ import annotations

import csv
import io
import json
import re
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path

import httpx

DEG_ANNOTATION_URL = "http://tubic.org/deg/public/download/deg_annotation_p.csv.zip"
REFERENCE_ORGANISM = "Streptococcus agalactiae"
REFERENCE_STRAIN = ""
DEG_ENTRY = ""
MAX_RETRIES = 3
REQUEST_TIMEOUT = 90.0

# Disk cache so the annotation file is downloaded at most once per machine.
_CACHE_DIR = Path(__file__).resolve().parents[2] / ".cache"

_annotation_cache: dict[str, list["EssentialGene"]] = {}


@dataclass(frozen=True)
class EssentialGene:
    deg_entry: str
    gene_id: str
    gi: int  # NCBI protein UID, used to restrict remote BLAST
    locus_tag: str
    product: str
    organism: str


def _column(row: list[str], index: int) -> str:
    value = row[index].strip() if index < len(row) else ""
    return value.strip('"').strip()


def _parse_annotation(
    text: str,
    *,
    organism: str | None,
    deg_entry: str = "",
) -> list[EssentialGene]:
    genes: list[EssentialGene] = []
    reader = csv.reader(io.StringIO(text), delimiter=";", quotechar='"')
    for row in reader:
        if not row or len(row) < 9:
            continue
        entry = _column(row, 0)
        if deg_entry and entry != deg_entry:
            continue
        row_organism = _column(row, 7)
        if organism and organism not in row_organism:
            continue
        gi_raw = _column(row, 3)
        if not gi_raw.startswith("GI:"):
            continue
        gi = int(re.sub(r"\D", "", gi_raw))
        locus_raw = _column(row, 10)
        locus = locus_raw.replace("locus_tag:", "").strip() if locus_raw else ""
        genes.append(
            EssentialGene(
                deg_entry=entry,
                gene_id=_column(row, 1),
                gi=gi,
                locus_tag=locus,
                product=_column(row, 6),
                organism=row_organism,
            )
        )
    return list({gene.gi: gene for gene in genes}.values())


def _to_serializable(genes: list[EssentialGene]) -> dict:
    return {"fetchedAt": time.time(), "genes": [g.__dict__ for g in genes]}


def _cache_path(organism: str | None) -> Path:
    scope = "all" if organism is None else re.sub(r"[^a-z0-9]+", "_", organism.lower()).strip("_")
    return _CACHE_DIR / f"deg_annotation_{scope}.json"


def _from_disk(organism: str | None) -> list[EssentialGene] | None:
    path = _cache_path(organism)
    if not path.exists():
        return None
    data = json.loads(path.read_text())
    fetched = data.get("fetchedAt") or 0
    if time.time() - fetched > 7 * 24 * 3600:
        return None  # stale after a week — refresh
    return [EssentialGene(**g) for g in data["genes"]]


def _write_disk(organism: str | None, genes: list[EssentialGene]) -> None:
    path = _cache_path(organism)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_to_serializable(genes)))


async def _fetch_annotation_text() -> str:
    async with httpx.AsyncClient(
        timeout=REQUEST_TIMEOUT,
        follow_redirects=True,
    ) as client:
        last_error: Exception | None = None
        for attempt in range(MAX_RETRIES):
            try:
                response = await client.get(DEG_ANNOTATION_URL)
                response.raise_for_status()
                with zipfile.ZipFile(io.BytesIO(response.content)) as zf:
                    # Pick the real CSV, skipping the __MACOSX cruft.
                    name = next(n for n in zf.namelist() if n.endswith(".csv") and not n.startswith("__MACOSX"))
                    return zf.read(name).decode("utf-8", errors="replace")
            except httpx.HTTPStatusError as exc:
                last_error = exc
                if exc.response.status_code < 500 and attempt < MAX_RETRIES - 1:
                    time.sleep(1.5 * (2**attempt))
                    continue
            except httpx.HTTPError as exc:
                last_error = exc
                time.sleep(1.5 * (2**attempt))
        raise ConnectionError(
            f"DEG annotation unreachable after {MAX_RETRIES} attempts: {last_error}"
        )


async def fetch_essential_genes(
    *,
    force_refresh: bool = False,
    organism: str | None = REFERENCE_ORGANISM,
) -> list[EssentialGene]:
    """Return all DEG proteins, or the organism-specific subset."""
    if organism in _annotation_cache and not force_refresh:
        return _annotation_cache[organism]

    cached = _from_disk(organism) if not force_refresh else None
    if cached is not None:
        _annotation_cache[organism] = cached
        return cached

    text = await _fetch_annotation_text()
    genes = _parse_annotation(
        text,
        organism=organism,
        deg_entry="",
    )
    if not genes:
        raise RuntimeError(
            f"Parsed 0 essential genes for {organism or 'all organisms'} (DEG) — "
            "DEG annotation schema may have changed."
        )
    _annotation_cache[organism] = genes
    _write_disk(organism, genes)
    return genes


def essential_uids(genes: list[EssentialGene]) -> list[int]:
    """NCBI protein UIDs that a remote BLAST search should be restricted to."""
    return [g.gi for g in genes]

def reference_label(organism: str | None = REFERENCE_ORGANISM) -> str:
    return "DEG all organisms" if organism is None else f"DEG species-wide ({organism})"
