"""Local prediction of cytokine-inducing epitopes for HTL (CD4+ T-cell) responses.

Implements:
- IFNepitope (IFN-gamma inducer): MHC-II anchor rules + IFN motif scanning
- IL4Pred (IL-4 inducer): Th2 promoting motif library  
- IL10Pred (IL-10 inducer): Regulatory T-cell motif detection
- VaxiJen for HTL: ACC-based antigenicity
- AlgPred for HTL: FAO/WHO allergen rules
- ToxinPred for HTL: Toxin motif scanning

MHC-II binding is approximated with consensus anchor residues:
- HLA-DRB1*04:01 — prefers Glu/Leu at P1, Pro at P6, hydrophobic at P9
- HLA-DRB1*15:01 — prefers Tyr/Val at P1, Pro at P6
- Others: moderate binders

References:
- Nielsen M et al. (2000). "Quantitative predictions of peptide binding motifs"
- Sturniolo T et al. (1999). "Multiple dependencies..."
- Gupta RS, colleagues (2017). "IFNepitope - database of IFN-inducing epitopes"
"""
from __future__ import annotations

from .vaxijen_local import predict_antigenicity, THRESHOLDS
from .algpred_local import predict_allergenicity, predict_toxicity
from .cytokine_local import (
    predict_ifn_gamma_epitope,
    predict_il4_epitope,
    predict_il10_epitope,
    HLA_SUPERCLASS_II,
    MHC_II_BINDING_THRESHOLD,
)


def predict_htl_ifn_epitope(
    sequence: str,
    hla_dr_alleles: list[str] | None = None,
    mhc_ii_ic50: dict[str, float] | None = None,
) -> dict:
    """Predict HTL IFN-gamma inducing epitope using MHC-II binding + motif rules.

    Extends CD8+ IFNepitope logic with MHC-II consensus binding:
    - P1: charged/hydrophobic anchor
    - P4: often Gln/Arg
    - P6: Pro (key anchor)
    - P9: hydrophobic

    Parameters
    ----------
    sequence : str
        HTL peptide (typically 13-20 aa).
    hla_dr_alleles : list[str] | None
        HLA-DR alleles.
    mhc_ii_ic50 : dict[str, float] | None
        Pre-computed MHC-II IC50 values per allele.

    Returns
    -------
    dict
        IFN-gamma induction prediction for HTL context.
    """
    hla = hla_dr_alleles or HLA_SUPERCLASS_II
    mhc_ii_ic50 = mhc_ii_ic50 or {}

    if not mhc_ii_ic50:
        for allele in hla:
            ic50 = _estimate_mhc_ii_binding(sequence, allele)
            mhc_ii_ic50[allele] = ic50

    binding_scores = {
        allele: 1.0 / (1.0 + max(ic50, 1.0) / MHC_II_BINDING_THRESHOLD)
        for allele, ic50 in mhc_ii_ic50.items()
    }
    best_binding = max(binding_scores.values()) if binding_scores else 0.0

    ifn_pred = predict_ifn_gamma_epitope(
        sequence,
        hla_alleles=hla,
        mhc_i_ic50=None,
    )

    length_factor = 1.0 if 13 <= len(sequence) <= 20 else 0.7
    total_score = (
        ifn_pred["inducer_score"] * 0.35
        + best_binding * 0.35
        + length_factor * 0.30
    )

    return {
        "inducer_score": round(total_score, 4),
        "is_inducer": total_score >= 0.45,
        "motifs_found": ifn_pred["motifs_found"],
        "mhc_ii_binding": {
            k: round(v, 4) for k, v in binding_scores.items()
        },
        "mhc_ii_ic50": mhc_ii_ic50,
        "hla_dr_context": hla,
        "method": "ifnepitope_htl_local",
    }


def predict_htl_il4_epitope(sequence: str) -> dict:
    """Predict HTL IL-4 (Th2) inducing epitope."""
    return predict_il4_epitope(sequence)


def predict_htl_il10_epitope(sequence: str) -> dict:
    """Predict HTL IL-10 (regulatory) inducing epitope."""
    return predict_il10_epitope(sequence)


def predict_vaxijen_htl(sequence: str) -> dict:
    """VaxiJen antigenicity prediction for HTL context."""
    score = predict_antigenicity(sequence, "bacteria")
    threshold = THRESHOLDS["bacteria"]
    return {
        "is_antigenic": score >= threshold,
        "score": score,
        "threshold": threshold,
        "method": "acc_local_htl",
    }


def predict_algpred_htl(
    sequence: str,
    pfam_domains: list[str] | None = None,
    reference_allergen_match: float = 0.0,
) -> dict:
    """AlgPred allergenicity prediction for HTL context."""
    result = predict_allergenicity(sequence, pfam_domains, reference_allergen_match)
    result["method"] = "iao_rules_local_htl"
    return result


def predict_toxinpred_htl(sequence: str) -> dict:
    """ToxinPred toxicity prediction for HTL context."""
    result = predict_toxicity(sequence)
    result["method"] = "motif_scan_local_htl"
    return result


def _estimate_mhc_ii_binding(sequence: str, hla_dr: str) -> float:
    """Estimate MHC-II binding (IC50) using DRB1 anchor residue rules.

    Anchor positions: P1, P4, P6, P9 (relative to binding groove 9-mer core).
    """
    seq = sequence.upper().strip()
    if len(seq) < 9:
        return 99999.0

    windows = [seq[i:i+9] for i in range(len(seq) - 8)]
    if not windows:
        return 99999.0

    drb1_anchors: dict[str, dict[int, set[str]]] = {
        "DRB1*04:01": {
            1: set("QEALK"),
            4: set("QKR"),
            6: set("P"),
            9: set("FYLVIW"),
        },
        "DRB1*04:02": {
            1: set("QEALK"),
            4: set("QKR"),
            6: set("P"),
            9: set("FYLVIW"),
        },
        "DRB1*15:01": {
            1: set("YVFW"),
            4: set("QKR"),
            6: set("P"),
            9: set("FYLVIW"),
        },
        "DRB1*03:01": {
            1: set("AKR"),
            4: set("S"),
            6: set("P"),
            9: set("FYLVIW"),
        },
    }

    allele_key = "DRB1*04:01"
    for prefix, anchors in drb1_anchors.items():
        if prefix in hla_dr:
            allele_key = prefix
            break

    anchors = drb1_anchors.get(allele_key, drb1_anchors["DRB1*04:01"])

    best_score = float("inf")
    for window in windows:
        matches = 0.0
        for pos, residues in anchors.items():
            if pos - 1 < len(window):
                if window[pos - 1] in residues:
                    matches += 1.0
        score = matches / len(anchors) if anchors else 0.5
        ic50 = 500.0 / (0.01 + score)
        best_score = min(best_score, ic50)

    return round(best_score, 2)
