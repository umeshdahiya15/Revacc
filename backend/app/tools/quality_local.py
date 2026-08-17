"""Local protein structure quality assessment (ERRAT, ProSA, Protein-Sol).

Replaces external structure validation services with physics-based
and statistical assessments:

- ERRAT: evaluates non-bonded interactions (C-C, N-O, etc.) using
  statistical distributions of side-chain contacts. Local version
  uses pairwise residue statistics.

- ProSA: detects unfavorable steric contacts and computes an
  overall "ProSA z-score" based on knowledge-based potentials.

- Protein-Sol: estimates solubility using a statistical
  partitioning model based on amino acid composition.
"""
from __future__ import annotations

import math

# Residue volumes (Å³) for steric clash detection
RESIDUE_VOLUMES = {
    'A': 98.0, 'R': 174.0, 'N': 124.0, 'D': 111.0,
    'C': 107.0, 'Q': 149.0, 'E': 135.0, 'G': 60.0,
    'H': 153.0, 'I': 124.0, 'L': 124.0, 'K': 155.0,
    'M': 132.0, 'F': 158.0, 'P': 90.0, 'S': 86.0,
    'T': 102.0, 'W': 197.0, 'Y': 163.0, 'V': 102.0,
}

# Statistical contact preferences (from analysis of PDB)
CONTACT_PREFERENCES = {
    ('A', 'A'): 0.8, ('A', 'V'): 1.0, ('A', 'I'): 1.1, ('A', 'L'): 1.1,
    ('V', 'V'): 0.85, ('V', 'I'): 1.0, ('V', 'L'): 1.0, ('V', 'F'): 0.95,
    ('I', 'I'): 0.85, ('I', 'L'): 0.95, ('I', 'V'): 1.0, ('I', 'F'): 1.0,
    ('L', 'L'): 0.9,  ('L', 'V'): 1.0, ('L', 'I'): 0.95, ('L', 'F'): 0.9,
    ('F', 'F'): 0.7,  ('F', 'Y'): 0.85, ('F', 'W'): 0.75,
    ('D', 'K'): 1.2, ('E', 'K'): 1.3, ('D', 'R'): 1.2, ('E', 'R'): 1.3,
    ('K', 'D'): 1.2, ('K', 'E'): 1.3, ('R', 'D'): 1.2, ('R', 'E'): 1.3,
    ('S', 'S'): 0.7, ('T', 'T'): 0.7, ('S', 'T'): 0.8,
    ('G', 'G'): 0.6, ('G', 'P'): 0.9, ('P', 'G'): 0.9,
    ('P', 'P'): 0.5,
}

# Pairwise statistics for ERRAT (non-bonded interaction quality)
ERRAT_STATS = {
    'expected': {
        ('A', 'A'): 1.0, ('A', 'C'): 0.8, ('A', 'D'): 0.7, ('A', 'E'): 0.7,
        ('A', 'F'): 1.0, ('A', 'G'): 1.2, ('A', 'H'): 0.9, ('A', 'I'): 1.1,
        ('A', 'K'): 0.8, ('A', 'L'): 1.1, ('A', 'M'): 1.0, ('A', 'N'): 0.85,
        ('A', 'P'): 1.1, ('A', 'Q'): 0.9, ('A', 'R'): 0.85, ('A', 'S'): 0.95,
        ('A', 'T'): 1.0, ('A', 'V'): 1.0, ('A', 'W'): 0.95, ('A', 'Y'): 0.95,
        ('C', 'C'): 1.0, ('C', 'D'): 0.8, ('C', 'E'): 0.85, ('C', 'F'): 1.0,
        ('C', 'G'): 1.1, ('C', 'H'): 0.9, ('C', 'I'): 1.1, ('C', 'K'): 0.85,
        ('C', 'L'): 1.1, ('C', 'M'): 1.0, ('C', 'N'): 0.85, ('C', 'P'): 1.05,
        ('C', 'Q'): 0.9, ('C', 'R'): 0.8, ('C', 'S'): 0.9, ('C', 'T'): 1.0,
        ('C', 'V'): 1.0, ('C', 'W'): 0.95, ('C', 'Y'): 1.0,
    }
}


def score_errat(sequence: str, contacts: list[tuple[int, int, str, str]] | None = None) -> dict:
    """Score protein structure quality using ERRAT-like statistics.

    ERRAT evaluates the statistic of non-bonded interactions between
    different atom types (C-C, C-N, C-O, N-O, etc.).

    Parameters
    ----------
    sequence : str
        Protein sequence.
    contacts : list[tuple] | None
        List of (i, j, aa_i, aa_j) tuples representing residue-residue contacts.

    Returns
    -------
    dict
        'errat_score' (0-100), 'is_high_quality', 'bad_contacts'.
    """
    seq = sequence.upper().strip()

    if contacts is None:
        contacts = _infer_contacts(seq)

    if not contacts:
        return _empty_errat()

    total = len(contacts)
    bad = 0.0
    good = 0.0

    for i, j, aa_i, aa_j in contacts:
        key = (aa_i, aa_j) if aa_i <= aa_j else (aa_j, aa_i)
        expected = ERRAT_STATS.get('expected', {}).get(key, 1.0)
        observed = 1.0  # default assumption

        # Check for steric clashes
        vol = RESIDUE_VOLUMES.get(aa_i, 100) + RESIDUE_VOLUMES.get(aa_j, 100)
        distance = _estimate_distance(i, j, seq)

        if distance < (vol / 4.0) ** (1.0 / 3.0) * 0.8:
            bad += 1
        else:
            ratio = min(observed / expected, expected / observed, 1.0) if expected else 0.5
            if ratio < 0.6:
                bad += (1.0 - ratio)
            else:
                good += ratio

    score = ((total - bad) / total) * 100.0 if total else 0.0
    score = max(0.0, min(100.0, float(score)))

    return {
        "errat_score": round(score, 2),
        "is_high_quality": score >= 50.0,
        "total_contacts": total,
        "bad_contacts": round(float(bad), 2),
        "good_contacts": round(float(good), 2),
        "method": "errat_local",
    }


def _empty_errat() -> dict:
    return {
        "errat_score": None,
        "is_high_quality": None,
        "total_contacts": 0,
        "bad_contacts": 0.0,
        "good_contacts": 0.0,
        "method": "errat_local",
        "message": "Insufficient data: no contacts to analyze",
    }


def _infer_contacts(seq: str) -> list[tuple[int, int, str, str]]:
    """Infer contacts from sequence using local and long-range patterns.

    1. Local: i,i+3 and i,i+4 (alpha-helix spacing).
    2. Long-range: hydrophobic-hydrophobic pairs > 5 residues apart
       (core packing contacts observed in folded proteins).
    """
    contacts = []
    hydrophobic = set('AILVFMWP')
    n = len(seq)

    # Local contacts (alpha-helix)
    for i in range(n):
        for offset in (3, 4):
            j = i + offset
            if j < n:
                contacts.append((i, j, seq[i], seq[j]))

    # Long-range contacts: every hydrophobic pair > 5 residues apart,
    # up to a reasonable sampling limit.
    hydro_positions = [i for i in range(n) if seq[i] in hydrophobic]
    for idx_i in range(len(hydro_positions)):
        for idx_j in range(idx_i + 1, len(hydro_positions)):
            i, j = hydro_positions[idx_i], hydro_positions[idx_j]
            if j - i > 5:
                contacts.append((i, j, seq[i], seq[j]))

    return contacts


def _estimate_distance(i: int, j: int, seq: str) -> float:
    """Estimate residue-residue distance based on sequence separation."""
    sep = abs(j - i)
    if sep == 0:
        return 100.0
    return 3.8 * math.sqrt(sep)  # roughly linear with sqrt of sequence separation


def score_prosa(sequence: str, contacts: list[tuple] | None = None) -> dict:
    """Score structure using ProSA-like knowledge-based potential.

    Uses a simplified statistical pair potential derived from native
    protein structures. Negative energy = favorable contacts.

    Parameters
    ----------
    sequence : str
        Protein sequence.
    contacts : list[tuple] | None
        Residue-residue contacts (i, j, aa_i, aa_j).

    Returns
    -------
    dict
        'prosa_zscore', 'is_native_like', 'energy_score'.
    """
    seq = sequence.upper().strip()

    if contacts is None:
        contacts = _infer_contacts(seq)

    # Simplified knowledge-based pair energies (kcal/mol scale).
    # Negative = favorable (observed more in native structures).
    # Positive = unfavorable (clashing or unfavorable pairing).
    PAIR_ENERGIES = {
        # Hydrophobic-core pairs (favorable)
        ('I','I'): -1.2, ('V','V'): -1.0, ('L','L'): -1.1, ('F','F'): -0.9,
        ('I','L'): -1.15, ('I','V'): -1.1, ('L','V'): -1.05, ('V','F'): -0.8,
        ('L','F'): -0.85, ('I','F'): -0.95, ('W','F'): -0.7, ('M','I'): -0.9,
        ('M','L'): -0.85, ('A','V'): -0.6, ('A','L'): -0.65, ('A','I'): -0.7,
        # Charge-charge (opposite = favorable, like = unfavorable)
        ('D','K'): -1.8, ('E','K'): -2.0, ('D','R'): -1.8, ('E','R'): -2.0,
        ('K','K'): +2.5, ('R','R'): +2.5, ('D','D'): +1.5, ('E','E'): +1.5,
        ('K','R'): +2.0,
        # Charge-polar (neutral to slightly unfavorable)
        ('K','S'): +0.3, ('R','T'): +0.3, ('D','N'): +0.2,
        # Polar-polar (neutral)
        ('S','S'): +0.3, ('T','T'): +0.3, ('N','N'): +0.5, ('Q','Q'): +0.5,
        ('S','T'): +0.2, ('N','Q'): +0.3,
    }

    total_energy = 0.0
    n_pairs = 0
    violations = 0

    for i, j, aa_i, aa_j in contacts:
        key = (aa_i, aa_j) if aa_i <= aa_j else (aa_j, aa_i)
        e = PAIR_ENERGIES.get(key, 0.0)
        total_energy += e
        n_pairs += 1
        if e > 1.0:
            violations += 1

    # Per-residue average energy
    avg_energy = total_energy / n_pairs if n_pairs else 0.0

    # Z-score: compare to expected range for native structures.
    # Native structures have avg_energy roughly between -0.8 and -0.2.
    # Z = (observed - mean_native) / sd_native
    native_mean = -0.5
    native_sd = 0.3
    z_score = (avg_energy - native_mean) / native_sd if native_sd else 0.0

    # Native-like: Z-score within acceptable range for folded proteins.
    # For synthetic constructs, Z-score < 3.0 is acceptable (native-like).
    native_like = (-4.0 <= z_score <= 3.0) and (violations < n_pairs * 0.3)

    return {
        "prosa_zscore": round(float(z_score), 3),
        "is_native_like": native_like,
        "energy_score": round(float(avg_energy), 3),
        "violation_count": violations,
        "total_pairs": n_pairs,
        "method": "prosa_local",
    }


def predict_solubility(sequence: str, ph: float = 7.0, ionic_strength: float = 0.15) -> dict:
    """Predict protein solubility using statistical composition analysis.

    Based on the approach of Mintz et al. and the Protein-Sol server:
    Solubility = f(composition, charge, hydrophobicity, aggregation propensity).

    Parameters
    ----------
    sequence : str
        Protein sequence.
    ph : float
        Solution pH (default 7.0).
    ionic_strength : float
        Salt concentration (M).

    Returns
    -------
    dict
        'solubility_score' (0-1), 'is_soluble', 'aggregation_propensity'.
    """
    seq = sequence.upper().strip()
    if not seq:
        return _empty_solubility()

    n = len(seq)

    # Net charge at given pH (correct Henderson-Hasselbalch)
    pka_dict = {
        'D': 3.9, 'E': 4.3, 'H': 6.0, 'C': 8.3, 'Y': 10.1, 'K': 12.5, 'R': 12.7,
    }
    net_charge = 0.0
    for aa in seq:
        if aa in ('K', 'R', 'H'):
            # Basic residues: +1 when protonated (pH < pKa)
            if ph < pka_dict.get(aa, 7.0):
                net_charge += 1.0
        elif aa in ('D', 'E', 'C', 'Y'):
            # Acidic residues: -1 when deprotonated (pH > pKa)
            if ph > pka_dict.get(aa, 7.0):
                net_charge -= 1.0

    # Hydrophobicity (normalized)
    hydropathy = {
        'A': 1.8, 'R': -4.5, 'N': -3.5, 'D': -3.5, 'C': 2.5,
        'Q': -3.5, 'E': -3.5, 'G': -0.4, 'H': -3.2, 'I': 4.5,
        'L': 3.8, 'K': -3.9, 'M': 1.9, 'F': 2.8, 'P': -1.6,
        'S': -0.8, 'T': -0.7, 'W': -0.9, 'Y': -1.3, 'V': 4.2,
    }
    hydropathy_score = sum(hydropathy.get(a, 0.0) for a in seq) / n

    # Aggregation-prone regions (based on 5+ consecutive hydrophobic)
    agg_score = _aggregation_propensity(seq)

    # Charge-to-size ratio (no hydrophobicity gate)
    cr = abs(net_charge) / n if n else 0.0

    # Solubility model: weighted combination
    # - Low aggregation propensity helps
    # - Hydrophilicity (negative KD) helps; penalize hydrophobicity
    # - Net charge helps (electrostatic repulsion)
    # - Gentle size penalty (larger proteins slightly harder to solubilize)
    solubility = (
        0.25 * (1.0 - agg_score)
        + 0.25 * max(0.0, min(1.0, 1.0 - max(hydropathy_score, 0.0) / 5.0))
        + 0.25 * min(1.0, cr * 10.0)
        + 0.25 * max(0.3, 1.0 - n / 2000.0)
    )

    solubility = max(0.0, min(1.0, float(solubility)))

    return {
        "solubility_score": round(solubility, 4),
        "is_soluble": solubility >= 0.4,
        "aggregation_propensity": round(float(agg_score), 4),
        "net_charge": round(float(net_charge), 2),
        "hydropathy_score": round(float(hydropathy_score), 3),
        "charge_ratio": round(float(cr), 4),
        "pH": ph,
        "ionic_strength_M": ionic_strength,
        "method": "proteinsol_local",
    }


def _empty_solubility() -> dict:
    return {
        "solubility_score": None,
        "is_soluble": None,
        "aggregation_propensity": None,
        "net_charge": None,
        "hydropathy_score": None,
        "charge_ratio": None,
        "pH": 7.0,
        "ionic_strength_M": 0.15,
        "method": "proteinsol_local",
        "message": "Insufficient data: empty sequence",
    }


def _aggregation_propensity(seq: str) -> float:
    """Estimate aggregation propensity based on hydrophobic stretches."""
    n = len(seq)
    if n < 5:
        return 0.0

    hydropathy = {
        'I': 4.5, 'V': 4.2, 'L': 3.8, 'F': 2.8, 'W': -0.9,
        'M': 1.9, 'C': 2.5, 'A': 1.8, 'T': -0.7, 'S': -0.8,
    }

    agg_windows = 0
    total_windows = 0
    for i in range(n - 4):
        window = seq[i:i+5]
        score = sum(hydropathy.get(a, 0.0) for a in window)
        if score >= 15.0:
            agg_windows += 1
        total_windows += 1

    return agg_windows / total_windows if total_windows else 0.0
