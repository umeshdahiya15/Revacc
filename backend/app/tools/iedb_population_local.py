"""Real population-coverage analysis via the IEDB Population Coverage 3.0.2
standalone tool (no mock, no web call).

The tool ships IEDB's own allele-frequency reference data (Allele Frequency
Net Database) — the same basis as the web IEDB-AR the paper used — and computes
coverage for a set of epitopes and their HLA alleles. We invoke it locally so
the 403-blocked web endpoint is not needed.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile

# Location of the unpacked standalone tool (downloaded once into the repo).
TOOL_DIR = os.environ.get(
    "IEDB_POPCOV_DIR",
    os.path.join(os.path.dirname(__file__), "..", "..", ".iedb_tools", "population_coverage"),
)
TOOL_DIR = os.path.abspath(TOOL_DIR)
SCRIPT = os.path.join(TOOL_DIR, "calculate_population_coverage.py")

# Areas the paper reports (global + the two peak regions).
DEFAULT_AREAS = ("World", "Europe", "North America")


def available() -> bool:
    return os.path.exists(SCRIPT)


def _build_input(epitopes: list[dict]) -> str:
    """Group alleles per epitope peptide -> tool input lines 'peptide<TAB>a,b,c'."""
    by_pep: dict[str, set[str]] = {}
    for e in epitopes:
        pep = (e.get("sequence") or e.get("peptide") or "").strip()
        allele = (e.get("hlaAllele") or "").strip()
        if not pep or not allele:
            continue
        by_pep.setdefault(pep, set()).add(allele)
    lines = [f"{pep}\t{','.join(sorted(alleles))}" for pep, alleles in by_pep.items()]
    return "\n".join(lines) + "\n"


def _parse_coverage(stdout: str) -> dict[str, float]:
    """Parse 'area  coverage%  average_hit  pc90' rows into {area: coverage%}."""
    out: dict[str, float] = {}
    for line in stdout.splitlines():
        m = re.match(r"^(.*?)\s+(\d+(?:\.\d+)?)%\s+[\d.]+\s+[\d.]+\s*$", line)
        if m:
            area = m.group(1).strip()
            if area.lower() in ("population/area", "average", "standard_deviation"):
                continue
            out[area] = float(m.group(2))
    return out


def compute(
    epitopes: list[dict],
    *,
    mhc_class: str = "combined",
    areas: tuple[str, ...] = DEFAULT_AREAS,
    python_exe: str | None = None,
) -> dict:
    """Run the real IEDB population-coverage tool. Returns coverage by area."""
    if not available():
        raise FileNotFoundError(f"IEDB population coverage tool not found at {SCRIPT}")

    text = _build_input(epitopes)
    if not text.strip():
        return {"coverage": 0.0, "by_area": {}, "message": "no epitopes/alleles", "method": "iedb_popcov_3.0.2"}

    py = python_exe or sys.executable
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, dir=TOOL_DIR) as fh:
        fh.write(text)
        infile = fh.name
    try:
        proc = subprocess.run(
            [py, SCRIPT, "-p", *areas, "-c", mhc_class, "-f", infile],
            capture_output=True, text=True, cwd=TOOL_DIR, timeout=600,
        )
    finally:
        try:
            os.remove(infile)
        except OSError:
            pass

    if proc.returncode != 0:
        raise RuntimeError(f"IEDB population coverage failed: {proc.stderr[:300]}")

    by_area = _parse_coverage(proc.stdout)
    world = by_area.get("World") or (next(iter(by_area.values())) if by_area else 0.0)
    return {
        "coverage": world,
        "by_area": by_area,
        "epitopes_analyzed": text.count("\n"),
        "mhc_class": mhc_class,
        "method": "iedb_popcov_3.0.2",
    }
