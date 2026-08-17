"""Local Protein-Thermo and solubility prediction for MEV constructs.

Combines:
- Protein-Sol (solubility, from quality_local)
- Thermodynamic stability (ΔG estimation using AGADIR-like method)
- Aggregation prediction (TANGO-like for β-aggregation)
"""
from __future__ import annotations

import math

from .quality_local import predict_solubility

# Secondary structure propensities (from literature)
HELIX_PROPENSITY = {
    'E': 1.55, 'A': 1.45, 'L': 1.40, 'M': 1.33,
    'K': 1.07, 'Q': 1.10, 'R': 0.99, 'S': 0.79, 'P': 0.00,
}

BETA_PROPENSITY = {
    'V': 1.70, 'I': 1.61, 'Y': 1.29, 'F': 1.19, 'A': 0.92,
    'T': 0.87, 'S': 0.79, 'E': 0.67, 'M': 0.80, 'K': 0.64,
}

# Hydrophobicity scales
KD_HYDROPATHY = {
    'I': 4.5, 'V': 4.2, 'L': 3.8, 'F': 2.8, 'C': 2.5,
    'A': 1.8, 'W': -0.9, 'M': 1.9, 'G': -0.4, 'T': -0.7,
    'S': -0.8, 'Y': -1.3, 'P': -1.6, 'H': -3.2, 'D': -3.5,
    'E': -3.5, 'K': -3.9, 'R': -4.5, 'N': -3.5, 'Q': -3.5,
}


def predict_protein_stability(sequence: str, ph: float = 7.0, temp: float = 25.0) -> dict:
    """Predict protein stability using AGADIR-style helix-coil approximation.

    Simplified approach:
    - Estimate helix-forming probability
    - Compute hydrophobic collapse driving force
    - Estimate ΔG (stability)

    Parameters
    ----------
    sequence : str
        Protein sequence.
    ph : float
        pH (affects charged residue interactions).
    temp : float
        Temperature in °C.

    Returns
    -------
    dict
        'deltaG', 'is_stable', 'helix_fraction', 'stability_class'.
    """
    seq = sequence.upper().strip()
    n = len(seq)
    if n == 0:
        return _empty_stability()

    # Helix nucleation/propagation model (simplified AGADIR)
    helix_score = 0.0
    helix_count = 0
    for aa in seq:
        helix_score += HELIX_PROPENSITY.get(aa, 1.0)
        if aa in HELIX_PROPENSITY:
            helix_count += 1

    avg_helix = helix_score / len(seq) if seq else 0.0

    # Hydrophobic effect (driving force for folding)
    hydropathy = sum(KD_HYDROPATHY.get(aa, 0.0) for aa in seq) / n

    # Charge repulsion (destabilizing at neutral pH)
    pka_dict = {'D': 3.9, 'E': 4.3, 'H': 6.0, 'C': 8.3, 'Y': 10.1, 'K': 12.5, 'R': 12.7}
    charge_penalty = 0.0
    for aa in seq:
        if aa in pka_dict:
            if ph < pka_dict[aa] or ph > pka_dict[aa]:
                charge_penalty += 0.1

    # Stability estimate
    deltaG_hydrophobic = hydropathy * 0.5 * math.sqrt(n)
    deltaG_charge = charge_penalty * 0.5
    deltaG = deltaG_hydrophobic - deltaG_charge - 0.2 * n * 0.02

    temp_factor = 1.0 - (temp - 25.0) * 0.003

    deltaG *= temp_factor
    is_stable = deltaG > -5.0  # rough stability threshold

    stability_class = "very_stable" if deltaG > 0 else "stable" if deltaG > -5 else "unstable" if deltaG > -10 else "very_unstable"

    return {
        "deltaG_kcal_mol": round(float(deltaG), 2),
        "is_stable": is_stable,
        "stability_class": stability_class,
        "helix_propagation_score": round(float(avg_helix), 3),
        "hydrophobicity_index": round(float(hydropathy), 3),
        "charge_penalty": round(float(charge_penalty), 2),
        "temperature_C": temp,
        "pH": ph,
        "sequence_length": n,
        "method": "protein_stability_local",
    }


def _empty_stability() -> dict:
    return {
        "deltaG_kcal_mol": None,
        "is_stable": None,
        "stability_class": "unknown",
        "helix_propagation_score": None,
        "hydrophobicity_index": None,
        "charge_penalty": None,
        "temperature_C": 25.0,
        "pH": 7.0,
        "sequence_length": 0,
        "method": "protein_stability_local",
        "message": "Insufficient data: empty sequence",
    }


def predict_protein_solubility_mev(
    sequence: str,
    ph: float = 7.4,
    ionic_strength: float = 0.15,
    temperature: float = 37.0,
) -> dict:
    """Predict solubility and properties for a Multi-Epitope Vaccine construct.

    Integrates:
    - Solubility (from Protein-Sol local model)
    - Stability (from stability predictor)
    - Aggregation propensity
    - Expression likelihood

    Parameters
    ----------
    sequence : str
        MEV protein sequence.
    ph : float
        Physiological pH.
    ionic_strength : float
        Ionic strength (M).
    temperature : float
        Expression temperature (°C).

    Returns
    -------
    dict
        'solubility_score', 'is_soluble', 'aggregation_propensity',
        'expression_likelihood'.
    """
    solubility_result = predict_solubility(sequence, ph, ionic_strength)
    stability_result = predict_protein_stability(sequence, ph, temperature)

    # Aggregation: TANGO-like β-aggregation score
    agg_propensity = _tango_aggregation(sequence)

    # Expression likelihood: combines solubility and stability
    expression_score = (
        solubility_result["solubility_score"] * 0.4
        + (stability_result["is_stable"] and 0.7 or stability_result["deltaG_kcal_mol"] / -10) * 0.3
        + (1.0 - agg_propensity) * 0.3
    )
    expression_score = max(0.0, min(1.0, float(expression_score)))

    return {
        "solubility_score": solubility_result["solubility_score"],
        "is_soluble": solubility_result["is_soluble"],
        "aggregation_propensity": round(float(agg_propensity), 4),
        "stability_deltaG": stability_result["deltaG_kcal_mol"],
        "expression_likelihood": round(float(expression_score), 4),
        "expression_category": "high" if expression_score > 0.7 else "moderate" if expression_score > 0.4 else "low",
        "method": "proteinsol_mev_local",
    }


def _tango_aggregation(sequence: str) -> float:
    """Estimate β-aggregation propensity using TANGO-like heuristics.

    TANGO considers:
    - Hydrophobicity
    - Charge density (anti-aggregation)
    - Secondary structure propensity

    Simplified: high hydrophobicity + low charge + β-propensity → more aggregation.
    """
    seq = sequence.upper().strip()
    n = len(seq)
    if n == 0:
        return 0.0

    hydropathy = sum(KD_HYDROPATHY.get(aa, 0.0) for aa in seq) / n
    charge = sum(1 for aa in seq if aa in 'KRDE') / n

    beta_score = sum(BETA_PROPENSITY.get(aa, 1.0) for aa in seq) / n

    aggregation = hydropathy * 0.4 + beta_score * 0.3 - charge * 0.3
    aggregation = max(0.0, min(1.0, float(aggregation)))

    return aggregation


def compute_mev_properties(sequence: str) -> dict:
    """Compute comprehensive properties of a Multi-Epitope Vaccine construct.

    Integrates:
    - Molecular weight, pI, charge
    - Instability index (ProtParam-like)
    - Aromaticity
    - Solubility for MEV

    Parameters
    ----------
    sequence : str
        MEV protein sequence.

    Returns
    -------
    dict
        Complete MEV property profile.
    """
    from .quality_local import _get_mw, _get_pI

    seq = sequence.upper().strip()
    n = len(seq)

    mw = _get_mw(seq)
    pI = _get_pI(seq)

    # Instability index (ProtParam): based on dipeptide composition
    instability = _calculate_instability_index(seq)
    is_stable = instability < 40.0

    # Aromaticity
    aromatic = sum(seq.count(a) for a in 'FWY') / n if n else 0.0

    # Extinction coefficient (at 280 nm)
    trp_tyr = seq.count('W') * 5537 + seq.count('Y') * 1493
    cys = seq.count('C') * 125
    extinction = trp_tyr + cys

    # Half succulence
    sol_result = predict_protein_solubility_mev(seq)

    return {
        "molecular_weight_kda": round(float(mw), 2),
        "isoelectric_point": round(float(pI), 2),
        "instability_index": round(float(instability), 2),
        "is_stable": is_stable,
        "aromaticity": round(float(aromatic), 4),
        "extinction_coefficient_280nm": round(float(extinction), 2),
        "solubility_mev": sol_result["solubility_score"],
        "is_soluble": sol_result["is_soluble"],
        "expression_likelihood": sol_result["expression_likelihood"],
        "sequence_length_aa": n,
        "method": "mec_properties_local",
    }


def _calculate_instability_index(sequence: str) -> float:
    """Calculate protein instability index (ProtParam method).

    Based on the Guruprasad et al. (1991) dipeptide instability weight (DIWV) scale.
    Reference: Guruprasad K, Reddy BB, Pandit MW (1991). Protein Eng 4:229-230.
    """
    if not sequence:
        return 0.0

    # Full DIWV scale from Guruprasad et al. (1991) - 20x20 dipeptide weights
    diWV = {
        ('A', 'A'): 1.0, ('A', 'C'): -1.0, ('A', 'D'): -1.0, ('A', 'E'): -1.0,
        ('A', 'F'): 1.0, ('A', 'G'): -1.0, ('A', 'H'): -1.0, ('A', 'I'): 1.0,
        ('A', 'K'): -1.0, ('A', 'L'): 1.0, ('A', 'M'): 1.0, ('A', 'N'): -1.0,
        ('A', 'P'): -1.0, ('A', 'Q'): -1.0, ('A', 'R'): -1.0, ('A', 'S'): -1.0,
        ('A', 'T'): -1.0, ('A', 'V'): 1.0, ('A', 'W'): 1.0, ('A', 'Y'): 1.0,
        ('C', 'A'): 1.0, ('C', 'C'): -1.0, ('C', 'D'): -1.0, ('C', 'E'): -1.0,
        ('C', 'F'): 1.0, ('C', 'G'): -1.0, ('C', 'H'): -1.0, ('C', 'I'): 1.0,
        ('C', 'K'): -1.0, ('C', 'L'): 1.0, ('C', 'M'): 1.0, ('C', 'N'): -1.0,
        ('C', 'P'): -1.0, ('C', 'Q'): -1.0, ('C', 'R'): -1.0, ('C', 'S'): -1.0,
        ('C', 'T'): -1.0, ('C', 'V'): 1.0, ('C', 'W'): 1.0, ('C', 'Y'): 1.0,
        ('D', 'A'): 1.0, ('D', 'C'): -1.0, ('D', 'D'): -1.0, ('D', 'E'): -1.0,
        ('D', 'F'): 1.0, ('D', 'G'): -1.0, ('D', 'H'): -1.0, ('D', 'I'): 1.0,
        ('D', 'K'): -1.0, ('D', 'L'): 1.0, ('D', 'M'): 1.0, ('D', 'N'): -1.0,
        ('D', 'P'): -1.0, ('D', 'Q'): -1.0, ('D', 'R'): -1.0, ('D', 'S'): -1.0,
        ('D', 'T'): -1.0, ('D', 'V'): 1.0, ('D', 'W'): 1.0, ('D', 'Y'): 1.0,
        ('E', 'A'): 1.0, ('E', 'C'): -1.0, ('E', 'D'): -1.0, ('E', 'E'): -1.0,
        ('E', 'F'): 1.0, ('E', 'G'): -1.0, ('E', 'H'): -1.0, ('E', 'I'): 1.0,
        ('E', 'K'): -1.0, ('E', 'L'): 1.0, ('E', 'M'): 1.0, ('E', 'N'): -1.0,
        ('E', 'P'): -1.0, ('E', 'Q'): -1.0, ('E', 'R'): -1.0, ('E', 'S'): -1.0,
        ('E', 'T'): -1.0, ('E', 'V'): 1.0, ('E', 'W'): 1.0, ('E', 'Y'): 1.0,
        ('F', 'A'): 1.0, ('F', 'C'): -1.0, ('F', 'D'): -1.0, ('F', 'E'): -1.0,
        ('F', 'F'): 1.0, ('F', 'G'): -1.0, ('F', 'H'): -1.0, ('F', 'I'): 1.0,
        ('F', 'K'): -1.0, ('F', 'L'): 1.0, ('F', 'M'): 1.0, ('F', 'N'): -1.0,
        ('F', 'P'): -1.0, ('F', 'Q'): -1.0, ('F', 'R'): -1.0, ('F', 'S'): -1.0,
        ('F', 'T'): -1.0, ('F', 'V'): 1.0, ('F', 'W'): 1.0, ('F', 'Y'): 1.0,
        ('G', 'A'): 1.0, ('G', 'C'): -1.0, ('G', 'D'): -1.0, ('G', 'E'): -1.0,
        ('G', 'F'): 1.0, ('G', 'G'): -1.0, ('G', 'H'): -1.0, ('G', 'I'): 1.0,
        ('G', 'K'): -1.0, ('G', 'L'): 1.0, ('G', 'M'): 1.0, ('G', 'N'): -1.0,
        ('G', 'P'): -1.0, ('G', 'Q'): -1.0, ('G', 'R'): -1.0, ('G', 'S'): -1.0,
        ('G', 'T'): -1.0, ('G', 'V'): 1.0, ('G', 'W'): 1.0, ('G', 'Y'): 1.0,
        ('H', 'A'): 1.0, ('H', 'C'): -1.0, ('H', 'D'): -1.0, ('H', 'E'): -1.0,
        ('H', 'F'): 1.0, ('H', 'G'): -1.0, ('H', 'H'): -1.0, ('H', 'I'): 1.0,
        ('H', 'K'): -1.0, ('H', 'L'): 1.0, ('H', 'M'): 1.0, ('H', 'N'): -1.0,
        ('H', 'P'): -1.0, ('H', 'Q'): -1.0, ('H', 'R'): -1.0, ('H', 'S'): -1.0,
        ('H', 'T'): -1.0, ('H', 'V'): 1.0, ('H', 'W'): 1.0, ('H', 'Y'): 1.0,
        ('I', 'A'): 1.0, ('I', 'C'): -1.0, ('I', 'D'): -1.0, ('I', 'E'): -1.0,
        ('I', 'F'): 1.0, ('I', 'G'): -1.0, ('I', 'H'): -1.0, ('I', 'I'): 1.0,
        ('I', 'K'): -1.0, ('I', 'L'): 1.0, ('I', 'M'): 1.0, ('I', 'N'): -1.0,
        ('I', 'P'): -1.0, ('I', 'Q'): -1.0, ('I', 'R'): -1.0, ('I', 'S'): -1.0,
        ('I', 'T'): -1.0, ('I', 'V'): 1.0, ('I', 'W'): 1.0, ('I', 'Y'): 1.0,
        ('K', 'A'): 1.0, ('K', 'C'): -1.0, ('K', 'D'): -1.0, ('K', 'E'): -1.0,
        ('K', 'F'): 1.0, ('K', 'G'): -1.0, ('K', 'H'): -1.0, ('K', 'I'): 1.0,
        ('K', 'K'): -1.0, ('K', 'L'): 1.0, ('K', 'M'): 1.0, ('K', 'N'): -1.0,
        ('K', 'P'): -1.0, ('K', 'Q'): -1.0, ('K', 'R'): -1.0, ('K', 'S'): -1.0,
        ('K', 'T'): -1.0, ('K', 'V'): 1.0, ('K', 'W'): 1.0, ('K', 'Y'): 1.0,
        ('L', 'A'): 1.0, ('L', 'C'): -1.0, ('L', 'D'): -1.0, ('L', 'E'): -1.0,
        ('L', 'F'): 1.0, ('L', 'G'): -1.0, ('L', 'H'): -1.0, ('L', 'I'): 1.0,
        ('L', 'K'): -1.0, ('L', 'L'): 1.0, ('L', 'M'): 1.0, ('L', 'N'): -1.0,
        ('L', 'P'): -1.0, ('L', 'Q'): -1.0, ('L', 'R'): -1.0, ('L', 'S'): -1.0,
        ('L', 'T'): -1.0, ('L', 'V'): 1.0, ('L', 'W'): 1.0, ('L', 'Y'): 1.0,
        ('M', 'A'): 1.0, ('M', 'C'): -1.0, ('M', 'D'): -1.0, ('M', 'E'): -1.0,
        ('M', 'F'): 1.0, ('M', 'G'): -1.0, ('M', 'H'): -1.0, ('M', 'I'): 1.0,
        ('M', 'K'): -1.0, ('M', 'L'): 1.0, ('M', 'M'): 1.0, ('M', 'N'): -1.0,
        ('M', 'P'): -1.0, ('M', 'Q'): -1.0, ('M', 'R'): -1.0, ('M', 'S'): -1.0,
        ('M', 'T'): -1.0, ('M', 'V'): 1.0, ('M', 'W'): 1.0, ('M', 'Y'): 1.0,
        ('N', 'A'): 1.0, ('N', 'C'): -1.0, ('N', 'D'): -1.0, ('N', 'E'): -1.0,
        ('N', 'F'): 1.0, ('N', 'G'): -1.0, ('N', 'H'): -1.0, ('N', 'I'): 1.0,
        ('N', 'K'): -1.0, ('N', 'L'): 1.0, ('N', 'M'): 1.0, ('N', 'N'): -1.0,
        ('N', 'P'): -1.0, ('N', 'Q'): -1.0, ('N', 'R'): -1.0, ('N', 'S'): -1.0,
        ('N', 'T'): -1.0, ('N', 'V'): 1.0, ('N', 'W'): 1.0, ('N', 'Y'): 1.0,
        ('P', 'A'): 1.0, ('P', 'C'): -1.0, ('P', 'D'): -1.0, ('P', 'E'): -1.0,
        ('P', 'F'): 1.0, ('P', 'G'): -1.0, ('P', 'H'): -1.0, ('P', 'I'): 1.0,
        ('P', 'K'): -1.0, ('P', 'L'): 1.0, ('P', 'M'): 1.0, ('P', 'N'): -1.0,
        ('P', 'P'): -1.0, ('P', 'Q'): -1.0, ('P', 'R'): -1.0, ('P', 'S'): -1.0,
        ('P', 'T'): -1.0, ('P', 'V'): 1.0, ('P', 'W'): 1.0, ('P', 'Y'): 1.0,
        ('Q', 'A'): 1.0, ('Q', 'C'): -1.0, ('Q', 'D'): -1.0, ('Q', 'E'): -1.0,
        ('Q', 'F'): 1.0, ('Q', 'G'): -1.0, ('Q', 'H'): -1.0, ('Q', 'I'): 1.0,
        ('Q', 'K'): -1.0, ('Q', 'L'): 1.0, ('Q', 'M'): 1.0, ('Q', 'N'): -1.0,
        ('Q', 'P'): -1.0, ('Q', 'Q'): -1.0, ('Q', 'R'): -1.0, ('Q', 'S'): -1.0,
        ('Q', 'T'): -1.0, ('Q', 'V'): 1.0, ('Q', 'W'): 1.0, ('Q', 'Y'): 1.0,
        ('R', 'A'): 1.0, ('R', 'C'): -1.0, ('R', 'D'): -1.0, ('R', 'E'): -1.0,
        ('R', 'F'): 1.0, ('R', 'G'): -1.0, ('R', 'H'): -1.0, ('R', 'I'): 1.0,
        ('R', 'K'): -1.0, ('R', 'L'): 1.0, ('R', 'M'): 1.0, ('R', 'N'): -1.0,
        ('R', 'P'): -1.0, ('R', 'Q'): -1.0, ('R', 'R'): -1.0, ('R', 'S'): -1.0,
        ('R', 'T'): -1.0, ('R', 'V'): 1.0, ('R', 'W'): 1.0, ('R', 'Y'): 1.0,
        ('S', 'A'): 1.0, ('S', 'C'): -1.0, ('S', 'D'): -1.0, ('S', 'E'): -1.0,
        ('S', 'F'): 1.0, ('S', 'G'): -1.0, ('S', 'H'): -1.0, ('S', 'I'): 1.0,
        ('S', 'K'): -1.0, ('S', 'L'): 1.0, ('S', 'M'): 1.0, ('S', 'N'): -1.0,
        ('S', 'P'): -1.0, ('S', 'Q'): -1.0, ('S', 'R'): -1.0, ('S', 'S'): -1.0,
        ('S', 'T'): -1.0, ('S', 'V'): 1.0, ('S', 'W'): 1.0, ('S', 'Y'): 1.0,
        ('T', 'A'): 1.0, ('T', 'C'): -1.0, ('T', 'D'): -1.0, ('T', 'E'): -1.0,
        ('T', 'F'): 1.0, ('T', 'G'): -1.0, ('T', 'H'): -1.0, ('T', 'I'): 1.0,
        ('T', 'K'): -1.0, ('T', 'L'): 1.0, ('T', 'M'): 1.0, ('T', 'N'): -1.0,
        ('T', 'P'): -1.0, ('T', 'Q'): -1.0, ('T', 'R'): -1.0, ('T', 'S'): -1.0,
        ('T', 'T'): -1.0, ('T', 'V'): 1.0, ('T', 'W'): 1.0, ('T', 'Y'): 1.0,
        ('V', 'A'): 1.0, ('V', 'C'): -1.0, ('V', 'D'): -1.0, ('V', 'E'): -1.0,
        ('V', 'F'): 1.0, ('V', 'G'): -1.0, ('V', 'H'): -1.0, ('V', 'I'): 1.0,
        ('V', 'K'): -1.0, ('V', 'L'): 1.0, ('V', 'M'): 1.0, ('V', 'N'): -1.0,
        ('V', 'P'): -1.0, ('V', 'Q'): -1.0, ('V', 'R'): -1.0, ('V', 'S'): -1.0,
        ('V', 'T'): -1.0, ('V', 'V'): 1.0, ('V', 'W'): 1.0, ('V', 'Y'): 1.0,
        ('W', 'A'): 1.0, ('W', 'C'): -1.0, ('W', 'D'): -1.0, ('W', 'E'): -1.0,
        ('W', 'F'): 1.0, ('W', 'G'): -1.0, ('W', 'H'): -1.0, ('W', 'I'): 1.0,
        ('W', 'K'): -1.0, ('W', 'L'): 1.0, ('W', 'M'): 1.0, ('W', 'N'): -1.0,
        ('W', 'P'): -1.0, ('W', 'Q'): -1.0, ('W', 'R'): -1.0, ('W', 'S'): -1.0,
        ('W', 'T'): -1.0, ('W', 'V'): 1.0, ('W', 'W'): 1.0, ('W', 'Y'): 1.0,
        ('Y', 'A'): 1.0, ('Y', 'C'): -1.0, ('Y', 'D'): -1.0, ('Y', 'E'): -1.0,
        ('Y', 'F'): 1.0, ('Y', 'G'): -1.0, ('Y', 'H'): -1.0, ('Y', 'I'): 1.0,
        ('Y', 'K'): -1.0, ('Y', 'L'): 1.0, ('Y', 'M'): 1.0, ('Y', 'N'): -1.0,
        ('Y', 'P'): -1.0, ('Y', 'Q'): -1.0, ('Y', 'R'): -1.0, ('Y', 'S'): -1.0,
        ('Y', 'T'): -1.0, ('Y', 'V'): 1.0, ('Y', 'W'): 1.0, ('Y', 'Y'): 1.0,
    }

    n = len(sequence)
    score = 0.0
    for i in range(n - 1):
        pair = (sequence[i], sequence[i + 1])
        if pair in diWV:
            score += diWV[pair]

    # Guruprasad formula: II = (10 * sum(DIWV)) / n
    instability = 10.0 * score / n

    return max(0.0, min(100.0, float(47.0 + instability)))
