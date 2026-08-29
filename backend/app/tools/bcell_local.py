"""Local B-cell epitope prediction using BepiPred propensity scores.

Based on:
- BepiPred-2.0 (DiscoTope approach): uses amino acid propensity scale
  and surface exposure prediction
- Parker's beta-turn prediction
- Karplus-Schulz flexibility scale
- Van Gunsteren hydrophilicity

Threshold: propensity score average >= 0.5 → local B-cell analysis.
We use a simplified version combining multiple scales.
"""
from __future__ import annotations

import re

# Karplus & Schulz (1982) flexibility scale (normalized 0-1)
FLEXIBILITY = {
    'A': 0.81, 'R': 0.81, 'N': 0.88, 'D': 0.63,
    'C': 0.81, 'Q': 0.88, 'E': 0.88, 'G': 0.43,
    'H': 0.74, 'I': 0.94, 'L': 0.94, 'K': 0.81,
    'M': 0.81, 'F': 0.84, 'P': 0.57, 'S': 0.63,
    'T': 0.76, 'W': 0.74, 'Y': 0.88, 'V': 0.91,
}

# Van Gunsteren hydrophilicity (1978), normalized
HYDROPHILICITY = {
    'A': 0.35, 'R': 0.79, 'N': 0.72, 'D': 0.72,
    'C': 0.24, 'Q': 0.71, 'E': 0.71, 'G': 0.43,
    'H': 0.58, 'I': 0.07, 'L': 0.10, 'K': 0.73,
    'M': 0.20, 'F': 0.20, 'P': 0.36, 'S': 0.62,
    'T': 0.58, 'W': 0.20, 'Y': 0.43, 'V': 0.29,
}

# Parker beta-turn scale (1978), normalized
BETA_TURN = {
    'A': 0.66, 'R': 0.72, 'N': 0.73, 'D': 0.67,
    'C': 0.74, 'Q': 0.73, 'E': 0.71, 'G': 0.88,
    'H': 0.74, 'I': 0.66, 'L': 0.71, 'K': 0.68,
    'M': 0.68, 'F': 0.71, 'P': 1.00, 'S': 0.82,
    'T': 0.80, 'W': 0.71, 'Y': 0.72, 'V': 0.69,
}

# Surface accessibility (Chou-Fasman, normalized)
SURFACE_ACCESS = {
    'A': 0.59, 'R': 0.59, 'N': 0.67, 'D': 0.67,
    'C': 0.59, 'Q': 0.67, 'E': 0.67, 'G': 0.59,
    'H': 0.59, 'I': 0.59, 'L': 0.59, 'K': 0.59,
    'M': 0.59, 'F': 0.59, 'P': 0.59, 'S': 0.67,
    'T': 0.67, 'W': 0.59, 'Y': 0.59, 'V': 0.59,
}

# Window size for averaging (BepiPred default)
WINDOW_SIZE = 7
THRESHOLD = 0.5


def _window_score(seq: str, pos: int, scale: dict, window: int = WINDOW_SIZE) -> float:
    """Compute windowed average score at position."""
    half = window // 2
    start = max(0, pos - half)
    end = min(len(seq), pos + half + 1)

    scores = []
    for i in range(start, end):
        aa = seq[i].upper()
        scores.append(scale.get(aa, 0.5))

    return sum(scores) / len(scores) if scores else 0.0


def predict_bepipred_epitope(sequence: str, window_size: int = WINDOW_SIZE) -> dict:
    """Predict linear B-cell epitope using multiple propensity scales.

    Combines:
    - Flexibility (Karplus-Schulz)
    - Hydrophilicity (Van Gunsteren)
    - Beta-turn (Parker)
    - Surface accessibility (Chou-Fasman)

    A position is epitope-positive if the combined score >= threshold.

    Parameters
    ----------
    sequence : str
        Protein sequence.
    window_size : int
        Window size for smoothing (default 7).

    Returns
    -------
    dict
        Epitope predictions: 'epitope_score', 'is_epitope', 'positions', 'fragments'.
    """
    seq = sequence.upper().strip()
    if not seq:
        return _empty_result()

    scores: list[float] = []
    positions: list[int] = []

    for i in range(len(seq)):
        flex = _window_score(seq, i, FLEXIBILITY, window_size)
        hydro = _window_score(seq, i, HYDROPHILICITY, window_size)
        turn = _window_score(seq, i, BETA_TURN, window_size)
        surf = _window_score(seq, i, SURFACE_ACCESS, window_size)

        combined = (flex * 0.25 + hydro * 0.25 + turn * 0.25 + surf * 0.25)
        combined = float(combined)  # ensure numpy-free type
        scores.append(round(combined, 4))

        if combined >= THRESHOLD:
            positions.append(i + 1)

    fragments = _extract_fragments(positions)

    avg_score = sum(scores) / len(scores) if scores else 0.0
    is_epitope = avg_score >= THRESHOLD or len(positions) > 0

    return {
        "epitope_score": round(avg_score, 4),
        "is_epitope": is_epitope,
        "epitope_positions": positions,
        "epitope_fragments": fragments,
        "position_wise_scores": scores,
        "threshold": THRESHOLD,
        "window_size": window_size,
        "method": "bepipred_local",
    }


def _extract_fragments(positions: list[int]) -> list[dict]:
    """Extract contiguous fragment ranges from position list."""
    if not positions:
        return []

    fragments = []
    start = positions[0]
    end = start
    for i in range(1, len(positions)):
        if positions[i] == end + 1:
            end = positions[i]
        else:
            fragments.append({"start": start, "end": end, "length": end - start + 1})
            start = positions[i]
            end = start
    fragments.append({"start": start, "end": end, "length": end - start + 1})
    return fragments


def _empty_result() -> dict:
    return {
        "epitope_score": 0.0,
        "is_epitope": False,
        "epitope_positions": [],
        "epitope_fragments": [],
        "position_wise_scores": [],
        "threshold": THRESHOLD,
        "window_size": WINDOW_SIZE,
        "method": "bepipred_local",
    }


def predict_ellipro_epitope(
    sequence: str,
    ig_domains: list[str] | None = None,
    residues_data: list[dict] | None = None,
) -> dict:
    """Predict discontinuous B-cell epitope (ElliPro-like).

    Ellipro predicts discontinuous epitopes by computing:
    - Average local surface accessibility (via residue-residue contacts)
    - Principal axis of the protein ellipsoid
    - Individual residue scores from structure

    Without a 3D structure, we approximate using:
    - Surface exposure propensity
    - Clustering of surface-exposed residues
    - Proximity of discontinuous segments

    Parameters
    ----------
    sequence : str
        Protein sequence.
    ig_domains : list[str] | None
        Immunoglobulin-like domain positions (if known).
    residues_data : list[dict] | None
        Per-residue annotations: [{'position': 1, 'surface_accessible': True, ...}, ...]

    Returns
    -------
    dict
        Discontinuous epitope prediction with 'discotope_score', 'is_epitope'.
    """
    seq = sequence.upper().strip()

    if residues_data and len(residues_data) == len(seq):
        surface_residues = sum(1 for r in residues_data if r.get("surface_accessible", False))
    else:
        surface_residues = sum(
            1 for aa in seq if SURFACE_ACCESS.get(aa.upper(), 0.5) >= 0.6
        )

    surface_ratio = surface_residues / len(seq) if seq else 0.0
    surface_ratio = float(surface_ratio)

    if ig_domains:
        domain_score = len(ig_domains) * 0.15
    else:
        domain_score = 0.0

    avg_score = (surface_ratio * 0.7 + domain_score * 0.3)
    is_epitope = avg_score >= 0.4

    # Extract contiguous surface-exposed fragments for conformational epitope candidates
    surface_positions = []
    for i, aa in enumerate(seq):
        if SURFACE_ACCESS.get(aa.upper(), 0.5) >= 0.6:
            surface_positions.append(i + 1)
    epitope_fragments = _extract_fragments(surface_positions)

    return {
        "discotope_score": round(avg_score, 4),
        "is_epitope": is_epitope,
        "surface_residue_ratio": round(surface_ratio, 4),
        "ig_domains_count": len(ig_domains) if ig_domains else 0,
        "epitope_fragments": epitope_fragments,
        "method": "ellipro_local",
    }


def score_b_epitope_immunogenicity(
    sequence: str,
    bepipred_score: float | None = None,
    ellipro_score: float | None = None,
) -> dict:
    """Score B-cell epitope immunogenicity (VaxiJen for B-cell context).

    B-cell epitopes are assessed by:
    - Surface exposure
    - Hydrophilicity
    - Lack of glycosylation (which masks epitopes)
    - Lack of Proline (breaks antibody contact)
    """
    seq = sequence.upper().strip()

    gly_motifs = len(re.findall(r"N[^P][ST]", seq))
    proline_count = seq.count("P")
    pro_ratio = proline_count / len(seq) if seq else 0.0

    if bepipred_score is None:
        bp = predict_bepipred_epitope(seq)
        bepipred_score = bp["epitope_score"]

    glycan_penalty = min(gly_motifs * 0.15, 0.5)
    proline_penalty = pro_ratio * 0.3

    final_score = max(0.0, (bepipred_score or 0.0) - glycan_penalty - proline_penalty)

    return {
        "immunogenicity_score": round(final_score, 4),
        "is_immunogenic": final_score >= 0.4,
        "glycosylation_sites": gly_motifs,
        "proline_ratio": round(pro_ratio, 4),
        "bepipred_score": bepipred_score,
        "ellipro_score": ellipro_score,
        "method": "b_epitope_immunogenicity_local",
    }
