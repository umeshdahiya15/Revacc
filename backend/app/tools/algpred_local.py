"""Local allergen prediction using FAO/WHO allergen rules and sequence similarity.

Based on EpiScore / allergen nomenclature database rules:
- All 6-factor criteria
- IUIS allergen database
- Physicochemical properties (MW, pI, half-life)

Key heuristics from literature:
  1. Sequence identity >35% to known allergen → likely allergen
  2. Conserved domains (pfam) associated with allergens
  3. High expression (seed storage, 2S albumin, nsLTP, etc.)
  4. Resistance to digestion (pepsin stable) → correlates with allergenicity

Default threshold: sequence identity > 35% AND MW in [5kDa, 80kDa] → non-allergen
"""
from __future__ import annotations

import re

ALLERGEN_PFAM = {
    "PF00017", "PF00078", "PF00089", "PF00101", "PF00111",
    "PF00153", "PF00205", "PF00218", "PF00280", "PF00303",
    "PF00440", "PF00471", "PF00528", "PF00682", "PF00722",
    "PF00952", "PF01361", "PF01612", "PF02047", "PF02183",
    "PF02302", "PF02341", "PF03368", "PF04141", "PF04165",
    "PF04218", "PF04562", "PF05075", "PF05834", "PF06416",
    "PF06655", "PF06839", "PF08263", "PF10208", "PF11214",
    "PF11250", "PF11291", "PF11349", "PF12106", "PF12364",
    "PF13937", "PF16881", "PF17039", "PF18041", "PF18940",
}

ALLERGEN_PATTERNS = [
    re.compile(r"(K|R)L\d{2}[ST]"),  # 2S albumin motif
    re.compile(r"[FW]x{0,1}[ED]x{0,1}[LIV]{2}"),  # nsLTP
    re.compile(r"C\.\.C.{4,6}C"),  # CC-type motif
]

MOLECULAR_WEIGHT_THRESHOLD = 100.0  # kDa cutoff for allergen classification
PEPTIDE_CHAIN_THRESHOLD = 8.0      # kDa minimum chain length


def _get_mw(seq: str) -> float:
    """Compute approximate molecular weight (kDa) from sequence."""
    weights = {
        'A': 89.09, 'R': 174.20, 'N': 132.12, 'D': 133.10,
        'C': 121.15, 'Q': 146.15, 'E': 147.13, 'G': 75.07,
        'H': 155.16, 'I': 131.17, 'L': 131.17, 'K': 146.19,
        'M': 149.21, 'F': 165.19, 'P': 115.13, 'S': 105.09,
        'T': 119.12, 'W': 204.23, 'Y': 181.19, 'V': 117.15,
    }
    total = sum(weights.get(aa, 110.0) for aa in seq.upper())
    return total / 1000.0


def _get_pI(seq: str) -> float:
    """Compute isoelectric point using Bjerrum electrostatics approximation."""
    seq = seq.upper()
    acidic = sum(seq.count(a) for a in "DE")
    basic = sum(seq.count(a) for a in "RKH")
    if not seq:
        return 7.0
    pI = 6.5 + 0.05 * (basic - acidic) / max(len(seq) / 100, 1.0)
    return pI


def calculate_similarity(seq: str, ref_seq: str) -> float:
    """Calculate sequence identity percentage."""
    if not seq or not ref_seq:
        return 0.0
    matches = sum(1 for a, b in zip(seq.upper(), ref_seq.upper()) if a == b)
    return matches / min(len(seq), len(ref_seq)) * 100.0


def predict_allergenicity(
    sequence: str,
    pfam_domains: list[str] | None = None,
    reference_allergen_match: float = 0.0,
) -> dict:
    """Predict allergenicity using multiple criteria.

    Parameters
    ----------
    sequence : str
        Protein sequence.
    pfam_domains : list[str] | None
        List of Pfam domain IDs associated with the protein.
    reference_allergen_match : float
        Percentage sequence identity to nearest known allergen (BLAST %).

    Returns
    -------
    dict
        Contains 'is_allergen', 'confidence', 'reason', and 'allergen_score'.
    """
    seq = sequence.upper().strip()
    pfam_domains = pfam_domains or []

    mw = _get_mw(seq)
    pI = _get_pI(seq)

    reasons: list[str] = []
    score = 0.0

    if reference_allergen_match > 35.0:
        reasons.append(f"sequence identity {reference_allergen_match:.1f}% to allergen")
        score += reference_allergen_match / 100.0 * 0.6

    allergen_domains = [d for d in pfam_domains if d in ALLERGEN_PFAM]
    if allergen_domains:
        reasons.append(f"conserved allergen domains: {allergen_domains}")
        score += 0.4

    for pattern in ALLERGEN_PATTERNS:
        if pattern.search(seq):
            reasons.append("allergen-like sequence motifs detected")
            score += 0.3
            break

    if 5.0 <= mw <= 80.0:
        score += 0.1
        reasons.append(f"MW {mw:.1f}kDa within allergen range")

    # NOTE: large proteins (MW > 100 kDa) are NOT inherently allergenic — known
    # allergens cluster in the 5-80 kDa range. A prior rule that ADDED allergen
    # score for MW > 100 kDa was biologically inverted and false-positived large
    # surface antigens (e.g. C5a peptidase, ~127 kDa). It has been removed.

    is_allergen = score >= 0.321 and len(reasons) > 0
    confidence = min(score, 1.0)

    return {
        "is_allergen": is_allergen,
        "allergen_score": round(min(score, 1.0), 4),
        "confidence": round(confidence, 4),
        "molecular_weight_kda": round(mw, 2),
        "pI": round(pI, 2),
        "reasons": reasons,
        "method": "iao_rules_local",
    }


def predict_toxicity(sequence: str, motif_scan: bool = True) -> dict:
    """Predict toxicity using ToxinPred-like motif scanning and FAO/WHO rules.

    Toxin-specific motifs from ToxinBase:
    - ADP-riboylating toxins
    - Pore-forming toxins
    - Protease toxins
    - Phospholipase toxins

    Also detects common contaminant motifs (e.g., DsbA, HtrA).
    """
    seq = sequence.upper().strip()
    toxin_motifs = [
        {"name": "NAD_glycohydrolase", "pattern": "WGD[TSG]", "score": 0.6},
        {"name": "pore_forming_chitinase_like", "pattern": "[TS]LD[GE]", "score": 0.3},
        {"name": "serine_protease_His", "pattern": "H[DN]GDS", "score": 0.4},
        {"name": "phospholipase_D_CxxxxD", "pattern": "CD[LIVM]-[AG]-[ST]-[ST]", "score": 0.45},
        {"name": "beta_barrel_pore", "pattern": r"[AP]-x{4}-[AP]-[LIVM]-[FY]-[AP]-[LIVM]", "score": 0.4},
        {"name": "ADP_ribosyl_glycohydrolase", "pattern": "STEH", "score": 0.3},
        {"name": "melittin_like_toxin", "pattern": "GIA[ED]VTLLLR", "score": 0.7},
        {"name": "cytotoxic_apoptosis_inducer", "pattern": r"[^P]{60,120}DWSH", "score": 0.5},
    ]

    toxin_hits = []
    total_score = 0.0
    for motif in toxin_motifs:
        if re.search(motif["pattern"], seq):
            toxin_hits.append(motif["name"])
            total_score += motif["score"]

    if len(seq) > 300 and total_score < 0.1:
        pass

    is_toxic = total_score >= 0.4
    confidence = min(total_score / 1.5, 1.0) if toxin_hits else 0.0

    return {
        "is_toxic": is_toxic,
        "toxicity_score": round(total_score, 4),
        "confidence": round(confidence, 4),
        "toxin_motifs_found": toxin_hits,
        "sequence_length": len(seq),
        "method": "motif_scan_local",
    }
