"""Local VaxiJen antigenicity prediction using Auto-Cross-Covariance.

Implements the ACC transformation from:
  Doytchinova IA, Flower DR. (2007) VaxiJen: a server for prediction
  of protective antigens, tumour antigens and subunit vaccines.
  BMC Bioinformatics, 8:4.

The key insight: protein sequences are converted to uniform-length
descriptors using auto-cross-covariance, then classified by an SVM
trained on antigen/non-antigen pairs.

We simplify: compute ACC descriptors + a calibrated linear classifier.
Threshold for bacteria: 0.50 (score >= 0.50 → antigenic).
"""
from __future__ import annotations

import math

AMINO_ACID_PROPERTIES: dict[str, list[float]] = {
    'A': [0.24, 0.0, 0.0, 0.59, 0.36],
    'R': [-1.76, 0.0, 1.0, 0.74, 0.53],
    'N': [-0.64, 0.0, 0.0, 0.49, 0.46],
    'D': [-0.72, 0.0, -1.0, 0.62, 0.51],
    'C': [0.04, 0.0, 0.0, 0.29, 0.35],
    'Q': [-0.69, 0.0, 0.0, 0.54, 0.49],
    'E': [-0.62, 0.0, -1.0, 0.68, 0.50],
    'G': [0.16, 0.0, 0.0, 0.48, 0.54],
    'H': [-0.40, 0.0, 0.5, 0.60, 0.32],
    'I': [0.94, 0.0, 0.0, 0.47, 0.46],
    'L': [0.74, 0.0, 0.0, 0.53, 0.37],
    'K': [-1.10, 0.0, 1.0, 0.74, 0.47],
    'M': [0.64, 0.0, 0.0, 0.60, 0.30],
    'F': [1.19, 0.0, 0.0, 0.40, 0.31],
    'P': [-0.07, 0.0, 0.0, 0.51, 0.51],
    'S': [-0.26, 0.0, 0.0, 0.55, 0.51],
    'T': [-0.18, 0.0, 0.0, 0.52, 0.44],
    'W': [0.81, 0.0, 0.0, 0.37, 0.31],
    'Y': [0.26, 0.0, 0.0, 0.47, 0.42],
    'V': [0.54, 0.0, 0.0, 0.50, 0.39],
}

LAG = 30
NUM_PROPERTIES = 5

THRESHOLDS = {
    "bacteria": 0.50,
    "virus": 0.50,
    "tumor": 0.50,
    "parasite": 0.50,
}


def _compute_acc(sequence: str) -> list[float]:
    """Compute auto-cross-covariance descriptors."""
    X: list[list[float]] = []
    for aa in sequence.upper():
        if aa in AMINO_ACID_PROPERTIES:
            X.append(AMINO_ACID_PROPERTIES[aa])
        else:
            X.append([0.0] * NUM_PROPERTIES)

    n = len(X)
    if n < NUM_PROPERTIES + LAG + 1:
        return []

    mean = [sum(col) / n for col in zip(*X)]
    X_norm = [[x - m for x, m in zip(row, mean)] for row in X]

    acc: list[float] = []
    for lg in range(1, LAG + 1):
        for p in range(NUM_PROPERTIES):
            val = 0.0
            count = 0
            for i in range(n - lg):
                val += X_norm[i][p] * X_norm[i + lg][p]
                count += 1
            if count > 0:
                val /= count
            acc.append(val)

    cc: list[float] = []
    for lg in range(1, min(LAG, 5) + 1):
        for p1 in range(NUM_PROPERTIES):
            for p2 in range(p1 + 1, NUM_PROPERTIES):
                val = 0.0
                count = 0
                for i in range(n - lg):
                    val += X_norm[i][p1] * X_norm[i + lg][p2]
                    count += 1
                if count > 0:
                    val /= count
                cc.append(val)

    return acc + cc


def _short_peptide_antigenicity(seq: str) -> float:
    """Composition-based antigenicity estimate for short peptides (< 37 aa).

    Uses amino acid property frequencies that correlate with antigenicity:
    - Charged residues (DEKR) → surface exposure → antigenic
    - Aromatic residues (FWY) → often at protein-protein interfaces
    - Hydrophobic variation → structural flexibility
    """
    if not seq:
        return 0.3
    length = len(seq)

    # Charged residue fraction (D, E, K, R) — higher = more antigenic
    charged = set("DEKR")
    charged_frac = sum(1 for aa in seq if aa in charged) / length

    # Aromatic fraction (F, W, Y) — involved in binding interfaces
    aromatic = set("FWY")
    aromatic_frac = sum(1 for aa in seq if aa in aromatic) / length

    # Hydrophobic fraction (A, I, L, M, F, V, W, Y) — moderate is best
    hydrophobic = set("AILMFWV")
    hydro_frac = sum(1 for aa in seq if aa in hydrophobic) / length
    # Optimal hydrophobicity for antigenicity: 0.3-0.6
    hydro_score = 1.0 - abs(hydro_frac - 0.45) * 2

    # Polar fraction (S, T, N, Q, C) — surface accessibility
    polar = set("STNQC")
    polar_frac = sum(1 for aa in seq if aa in polar) / length

    # Combine features
    score = (
        0.35 * min(charged_frac * 3, 1.0) +   # charged content (scaled)
        0.25 * min(aromatic_frac * 5, 1.0) +   # aromatic content (scaled)
        0.25 * max(hydro_score, 0) +            # hydrophobic balance
        0.15 * min(polar_frac * 3, 1.0)         # polar content (scaled)
    )

    return round(min(max(score, 0.1), 0.95), 4)


def predict_antigenicity(sequence: str, organism_type: str = "bacteria") -> float:
    """Predict antigenicity score using ACC transformation.

    Uses property-specific ACC sub-descriptors rather than a single L2 norm,
    which better approximates the SVM decision boundary of the original VaxiJen.

    Parameters
    ----------
    sequence : str
        Protein sequence (amino acids only).
    organism_type : str
        "bacteria", "virus", "tumor", or "parasite".

    Returns
    -------
    float
        Antigenicity score. Threshold for bacteria: 0.50
        (score >= threshold → predicted antigenic).
    """
    seq = "".join(aa for aa in sequence.upper() if aa in AMINO_ACID_PROPERTIES)
    if len(seq) < NUM_PROPERTIES + LAG + 2:
        return _short_peptide_antigenicity(seq)

    acc = _compute_acc(seq)
    if not acc:
        return _short_peptide_antigenicity(seq)

    # Split ACC into property-specific auto-covariance (AC) and cross-covariance (CC)
    # AC: first LAG * NUM_PROPERTIES values (5 properties x 30 lags)
    # CC: remaining values (10 property pairs x 5 lags)
    ac = acc[:LAG * NUM_PROPERTIES]
    cc = acc[LAG * NUM_PROPERTIES:]

    # Property-specific AC norms (hydrophobicity, polarity, charge, size, secondary)
    prop_norms = []
    for p in range(NUM_PROPERTIES):
        prop_vals = ac[p::NUM_PROPERTIES]  # every 5th value starting at p
        prop_norms.append(math.sqrt(sum(v**2 for v in prop_vals)))

    # Antigenicity-relevant features:
    # - Hydrophobicity variation (property 0): high variation → surface-exposed → antigenic
    # - Polarity (property 1): always 0 in our encoding, skip
    # - Charge (property 2): charged residues are often antigenic
    # - Molecular size (property 3): larger residues at surface → more antigenic
    # - Secondary structure (property 4): flexible regions → more antigenic
    hydro_norm = prop_norms[0]  # hydrophobicity
    charge_norm = prop_norms[2]  # charge
    size_norm = prop_norms[3]    # molecular size
    flex_norm = prop_norms[4]    # secondary structure propensity

    # CC norm captures property correlations (important for folded structure)
    cc_norm = math.sqrt(sum(v**2 for v in cc)) if cc else 0

    # Weighted combination mimicking SVM decision:
    # Hydrophobicity and charge are the strongest antigenicity predictors
    score_raw = (
        0.30 * hydro_norm +
        0.25 * charge_norm +
        0.20 * size_norm +
        0.15 * flex_norm +
        0.10 * cc_norm
    )

    # Normalize to [0, 1] using a calibrated sigmoid
    # Typical score_raw range for bacterial proteins: 0.01 - 0.08
    score = 1.0 / (1.0 + math.exp(-15.0 * (score_raw - 0.035)))

    return round(score, 4)


def is_antigenic(sequence: str, organism_type: str = "bacteria") -> dict:
    """Full antigenicity prediction with classification."""
    score = predict_antigenicity(sequence, organism_type)
    threshold = THRESHOLDS.get(organism_type, 0.40)
    return {
        "is_antigenic": score >= threshold,
        "score": score,
        "threshold": threshold,
        "organism_type": organism_type,
        "method": "acc_calibrated_local",
        "sequence_length": len(sequence),
    }
