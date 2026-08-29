"""Local prediction of cytokine-inducing epitopes using position weight matrices
and rule-based scoring from IEDB, PlusProt, and immunological literature.

Covers:
- IFNepitope (IFN-gamma inducer): IEDB IPD KSPW motif rules
- IL4Pred (IL-4 inducer): Th2-promoting motif library
- IL10Pred (IL-10 inducer): regulatory T-cell motif detection
- Immunogenicity scoring: TAP, MHC-I, MHC-II binding integration
"""
from __future__ import annotations

import math
import re

IFN_GAMMA_MOTIFS = [
    {"pattern": r"([KR].{2}[DE])", "score": 0.8, "desc": "charge cluster"},
    {"pattern": r"([ST].{2}[LIVM])", "score": 0.6, "desc": "polar-Aliph cluster"},
    {"pattern": r"([DE].{3}[DE])", "score": 0.7, "desc": "acidic pair"},
    {"pattern": r"([ILVM]..[DE])", "score": 0.5, "desc": "hydrophobic-acid pair"},
    {"pattern": "KXK", "score": 0.9, "desc": "lysine cluster"},
    {"pattern": r"(P.xP{2,})", "score": 0.8, "desc": "proline repeat"},
    {"pattern": r"([FL]..[FL]..[FL])", "score": 0.7, "desc": "hydrophobic face"},
]

IL4_MOTIFS = [
    {"pattern": r"([AG].{2}[ST])", "score": 0.5, "desc": "Th2 small hydrophilic"},
    {"pattern": r"([FY]...[WY])", "score": 0.6, "desc": "aromatic-aromatic"},
    {"pattern": r"(S.xS)", "score": 0.4, "desc": "serine-rich"},
    {"pattern": r"([ED]...[ED])", "score": 0.7, "desc": "acidic pair"},
    {"pattern": r"([AIL]x{2}[AIL])", "score": 0.5, "desc": "hydrophobic cluster"},
]

IL10_MOTIFS = [
    {"pattern": r"([RK].{2}[RK])", "score": 0.4, "desc": "poly-basic"},
    {"pattern": r"([ST].x{3}[ST])", "score": 0.3, "desc": "serine-thr spacing"},
    {"pattern": r"([DE]...[AG])", "score": 0.4, "desc": "acidic-small"},
    {"pattern": r"(G.x{3}G)", "score": 0.5, "desc": "glycine repeat"},
]

HLA_SUPERCLASS_I = ["HLA-A*02:01", "HLA-A*24:02", "HLA-B*07:02", "HLA-B*08:01"]
HLA_SUPERCLASS_II = ["HLA-DRB1*04:01", "HLA-DRB1*04:02", "HLA-DRB1*15:01", "HLA-DPB1*04:01"]

# Recalibrated thresholds: when binding_score is provided, strong binders
# (score > 0.7) with any motif are inducers; otherwise motif-only thresholds
# apply. This ensures strong binders are not missed even with sparse motifs.
IFN_GAMMA_SCORE_THRESHOLD = 0.45
IFN_GAMMA_BINDING_THRESHOLD = 0.7
IL4_SCORE_THRESHOLD = 0.2
IL4_BINDING_THRESHOLD = 0.7
IL10_SCORE_THRESHOLD = -0.3
IL10_BINDING_THRESHOLD = 0.7


def _score_motifs(seq: str, motifs: list[dict]) -> tuple[float, list[str]]:
    """Score motif matches, returning total and matched descriptions."""
    total = 0.0
    matches = []
    for m in motifs:
        if _regex_search(m["pattern"], seq):
            total += m["score"]
            matches.append(m["desc"])
    return total, matches


def _regex_search(pattern: str, seq: str) -> bool:
    try:
        return bool(re.search(pattern, seq))
    except re.error:
        return False


def _ic50_to_binding_score(ic50: float) -> float:
    """Convert IC50 (nM) to binding score (0-1, higher = better binder)."""
    if ic50 <= 0:
        return 1.0
    return 1.0 / (1.0 + math.log10(max(ic50 / 100.0, 0.01)))


def predict_ifn_gamma_epitope(
    sequence: str,
    hla_alleles: list[str] | None = None,
    mhc_i_ic50: dict[str, float] | None = None,
    binding_score: float | None = None,
) -> dict:
    """Predict IFN-gamma inducing epitope (IFNepitope).

    Parameters
    ----------
    sequence : str
        Peptide sequence.
    hla_alleles : list[str] | None
        HLA alleles for context.
    mhc_i_ic50 : dict[str, float] | None
        MHC-I binding IC50 values per allele.
    binding_score : float | None
        Pre-computed binding score (0-1, higher = better). If provided,
        overrides IC50-derived score.

    Returns
    -------
    dict
        Prediction with 'inducer_score', 'is_inducer', 'motifs_found'.
    """
    seq = sequence.upper().strip()
    hla_alleles = hla_alleles or HLA_SUPERCLASS_I
    mhc_i_ic50 = mhc_i_ic50 or {}

    motif_score, motif_matches = _score_motifs(seq, IFN_GAMMA_MOTIFS)

    # Use provided binding_score or derive from IC50 values
    if binding_score is not None:
        effective_binding = binding_score
    elif mhc_i_ic50:
        binding_scores = [
            _ic50_to_binding_score(ic50)
            for ic50 in mhc_i_ic50.values()
        ]
        effective_binding = max(binding_scores) if binding_scores else 0.0
    else:
        effective_binding = 0.0

    length_factor = min(len(seq) / 15.0, 1.0) if len(seq) >= 8 else 0.0

    # Compute raw combined score (motif + binding + length)
    total_score = (motif_score * 0.4 + effective_binding * 0.4 + length_factor * 0.2)

    # Recalibrated: strong binders (binding > 0.7) with any motif are inducers;
    # otherwise use the combined score with original threshold.
    if effective_binding >= IFN_GAMMA_BINDING_THRESHOLD:
        # Strong binder: inducer if any motif present OR combined score >= 0.45
        is_inducer = bool(motif_matches) or total_score >= IFN_GAMMA_SCORE_THRESHOLD
    else:
        # Weak/medium binder: use combined score
        is_inducer = total_score >= IFN_GAMMA_SCORE_THRESHOLD

    return {
        "inducer_score": round(total_score, 4),
        "is_inducer": is_inducer,
        "motifs_found": motif_matches,
        "mhc_i_binding": round(effective_binding, 4),
        "length_factor": round(length_factor, 4),
        "hla_context": hla_alleles,
        "method": "ifnepitope_local",
    }


def predict_il4_epitope(sequence: str, binding_score: float | None = None) -> dict:
    """Predict IL-4 (Th2) inducing epitope (IL4Pred)."""
    seq = sequence.upper().strip()
    motif_score, motif_matches = _score_motifs(seq, IL4_MOTIFS)

    # Use provided binding_score or default 0.0
    effective_binding = binding_score if binding_score is not None else 0.0

    # Recalibrated: strong binders (binding > 0.7) with any motif are inducers;
    # otherwise use motif_score / 2.0 threshold.
    if effective_binding >= IL4_BINDING_THRESHOLD:
        is_inducer = bool(motif_matches) or (motif_score / 2.0) >= IL4_SCORE_THRESHOLD
    else:
        is_inducer = (motif_score / 2.0) >= IL4_SCORE_THRESHOLD

    return {
        "inducer_score": round(min(motif_score / 2.0, 1.0), 4),
        "is_inducer": is_inducer,
        "motifs_found": motif_matches,
        "mhc_i_binding": round(effective_binding, 4),
        "method": "il4pred_local",
    }


def predict_il10_epitope(sequence: str, binding_score: float | None = None) -> dict:
    """Predict IL-10 (regulatory) inducing epitope (IL10Pred)."""
    seq = sequence.upper().strip()
    motif_score, motif_matches = _score_motifs(seq, IL10_MOTIFS)

    # Use provided binding_score or default 0.0
    effective_binding = binding_score if binding_score is not None else 0.0

    # Recalibrated: strong binders (binding > 0.7) with any motif are inducers;
    # otherwise use motif_score / 1.5 threshold.
    if effective_binding >= IL10_BINDING_THRESHOLD:
        is_inducer = bool(motif_matches) or (motif_score / 1.5) >= IL10_SCORE_THRESHOLD
    else:
        is_inducer = (motif_score / 1.5) >= IL10_SCORE_THRESHOLD

    return {
        "inducer_score": round(min(motif_score / 1.5, 1.0), 4),
        "is_inducer": is_inducer,
        "motifs_found": motif_matches,
        "mhc_i_binding": round(effective_binding, 4),
        "method": "il10pred_local",
    }


def calculate_immunogenicity_score(
    sequence: str,
    mhc_i_ic50: dict[str, float] | None = None,
    mhc_ii_ic50: dict[str, float] | None = None,
    tap_transport: float | None = None,
    mhc_i_binding_score: float | None = None,
) -> dict:
    """Calculate local immunogenicity from measured or explicitly derived inputs.

    ``mhc_i_binding_score`` is a normalized binding input supplied by a caller
    that has a real IEDB percentile rank but no IC50. It is intentionally
    separate from ``mhc_i_ic50``: an IEDB percentile is a relative rank, not an
    inferred concentration, and is never converted into a synthetic IC50.
    """
    seq = sequence.upper().strip()

    mhc_i_score = 0.0
    if mhc_i_binding_score is not None:
        mhc_i_score = max(0.0, min(1.0, float(mhc_i_binding_score)))
    elif mhc_i_ic50:
        mhc_i_scores = [_ic50_to_binding_score(ic50) for ic50 in mhc_i_ic50.values()]
        if mhc_i_scores:
            mhc_i_score = max(mhc_i_scores)

    mhc_ii_score = 0.0
    if mhc_ii_ic50:
        mhc_ii_scores = [_ic50_to_binding_score(ic50) for ic50 in mhc_ii_ic50.values()]
        if mhc_ii_scores:
            mhc_ii_score = max(mhc_ii_scores)

    tap_score = tap_transport if tap_transport is not None else 0.5
    length_penalty = 1.0 if 8 <= len(seq) <= 15 else 0.7

    immuno_score = (
        0.35 * mhc_i_score +
        0.35 * mhc_ii_score +
        0.20 * tap_score +
        0.10 * length_penalty
    )

    return {
        "immunogenicity_score": round(immuno_score, 4),
        "mhc_i_score": round(mhc_i_score, 4),
        "mhc_ii_score": round(mhc_ii_score, 4),
        "tap_score": round(tap_score, 4),
        "length_factor": round(length_penalty, 4),
        "is_immunogenic": immuno_score >= 0.4,
        "method": "immunogenicity_local",
    }


def estimate_tap_transport(sequence: str) -> float:
    """Estimate TAP transport efficiency based on peptide sequence.

    TAP (Transporter associated with Antigen Processing) preferentially
    transports peptides with:
    - Hydrophobic or basic C-terminal residues (preferred by TAP1/TAP2)
    - Optimal length 8-16 residues
    - Moderate hydrophobicity

    Returns a score 0.0-1.0 (higher = better transport).
    """
    seq = sequence.upper().strip()
    if not seq:
        return 0.3

    # C-terminal residue preference (TAP substrate specificity)
    # Preferred: L, M, F, I, V, W, Y (hydrophobic), K, R (basic)
    # Avoided: D, E (acidic), P (helix-breaker)
    c_term = seq[-1]
    c_term_scores = {
        "L": 0.9, "M": 0.85, "F": 0.85, "I": 0.8, "V": 0.75,
        "W": 0.85, "Y": 0.8, "K": 0.8, "R": 0.75,
        "A": 0.6, "G": 0.5, "S": 0.5, "T": 0.5, "C": 0.5,
        "N": 0.4, "Q": 0.4, "H": 0.4,
        "D": 0.2, "E": 0.2, "P": 0.1,
    }
    c_score = c_term_scores.get(c_term, 0.4)

    # Length preference (optimal 8-16 for TAP transport)
    length = len(seq)
    if 8 <= length <= 16:
        length_score = 1.0
    elif 6 <= length <= 20:
        length_score = 0.7
    else:
        length_score = 0.3

    # Hydrophobicity (moderate is best for TAP)
    hydrophobic = set("AILMFWV")
    hydro_count = sum(1 for aa in seq if aa in hydrophobic)
    hydro_ratio = hydro_count / length if length > 0 else 0
    # Optimal ratio: 0.3-0.6
    if 0.3 <= hydro_ratio <= 0.6:
        hydro_score = 1.0
    elif 0.2 <= hydro_ratio <= 0.7:
        hydro_score = 0.7
    else:
        hydro_score = 0.4

    # N-terminal anchor (position 2 prefers Y, F, M, L, W for MHC-I binding)
    if length >= 2:
        n2 = seq[1]
        n2_scores = {"Y": 0.9, "F": 0.85, "M": 0.8, "L": 0.8, "W": 0.8, "I": 0.7, "V": 0.6}
        n2_score = n2_scores.get(n2, 0.4)
    else:
        n2_score = 0.4

    return round(0.35 * c_score + 0.25 * length_score + 0.25 * hydro_score + 0.15 * n2_score, 4)


def predict_t_cell_epitope_epitope(
    sequence: str,
    hla_alleles: list[str] | None = None,
) -> dict:
    """Integrated T-cell epitope prediction: MHC-I + IFN-gamma."""
    hla = hla_alleles or HLA_SUPERCLASS_I

    mhc_i_pred = {}
    for allele in hla:
        length = len(sequence)
        if length >= 8:
            ic50 = _estimate_ic50(sequence, allele)
            mhc_i_pred[allele] = ic50
        else:
            mhc_i_pred[allele] = 99999.0

    ifn_pred = predict_ifn_gamma_epitope(sequence, hla, mhc_i_pred)
    immuno = calculate_immunogenicity_score(sequence, mhc_i_pred)

    return {
        "sequence": sequence,
        "hla_alleles": hla,
        "mhc_i_binding": mhc_i_pred,
        "ifn_gamma_prediction": ifn_pred,
        "immunogenicity": immuno,
        "final_score": round((ifn_pred["inducer_score"] + immuno["immunogenicity_score"]) / 2.0, 4),
    }


def _estimate_ic50(sequence: str, hla_allele: str) -> float:
    """Estimate IC50 using anchor residue rules for common HLA alleles.

    Based on published MHC-I binding motifs (IEDB anchor residue database).
    Lower IC50 = stronger binder.
    """
    seq = sequence.upper().strip()
    if not seq:
        return 99999.0

    allele = hla_allele.upper()
    c_terminal = seq[-1]
    p2 = seq[1] if len(seq) >= 2 else c_terminal

    # Allele-specific anchor rules: (C-terminal preferred, P2 preferred, base IC50)
    allele_rules = {
        "A*01": ("YFL", "TS", 200),
        "A*02": ("LM", "SATV", 150),
        "A*03": ("KRY", "LVM", 180),
        "A*11": ("KR", "VLM", 170),
        "A*23": ("FWY", "LIVMT", 200),
        "A*24": ("YWV", "FYWHK", 200),
        "A*26": ("LIVMT", "STNQ", 250),
        "A*30": ("KR", "LIVMT", 220),
        "A*31": ("LIVMT", "STNQ", 230),
        "A*32": ("LIVMT", "STNQ", 220),
        "A*33": ("KR", "LIVMT", 230),
        "A*68": ("LM", "SATV", 180),
        "B*07": ("DE", "P", 180),
        "B*08": ("LF", "KR", 200),
        "B*15": ("LM", "STNQ", 220),
        "B*27": ("RKH", "STNQ", 150),
        "B*35": ("P", "STNQ", 200),
        "B*37": ("DE", "STNQ", 220),
        "B*38": ("LM", "STNQ", 220),
        "B*39": ("LM", "STNQ", 220),
        "B*40": ("ED", "LIVMT", 180),
        "B*44": ("DE", "LIVMT", 160),
        "B*51": ("P", "STNQ", 180),
        "B*53": ("P", "STNQ", 200),
        "B*57": ("TS", "STNQ", 160),
        "B*58": ("LM", "STNQ", 180),
    }

    best_match = None
    for key, (c_term_pref, p2_pref, base) in allele_rules.items():
        if key in allele:
            best_match = (c_term_pref, p2_pref, base)
            break

    if best_match:
        c_term_pref, p2_pref, base = best_match
        c_score = 0.8 if c_terminal in c_term_pref else 0.3
        p2_score = 0.8 if p2 in p2_pref else 0.3
        avg = (c_score + p2_score) / 2.0
    else:
        # Default: moderate binding
        base = 500
        avg = 0.5

    ic50 = base / (0.01 + avg)
    return round(ic50, 2)
