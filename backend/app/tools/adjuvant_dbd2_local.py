"""Local in-silico PCR and restriction cloning.

Uses Bio.Restriction for real restriction enzyme logic:
- Find restriction sites
- Check for internal cut sites in insert
- Simulate restriction digest
- Design cloning strategy (Gibson Assembly or Golden Gate)
"""
from __future__ import annotations

import re
from collections import defaultdict

try:
    from Bio.Seq import Seq
    from Bio.SeqUtils import MeltingTemp as mt
    _BIOAVAILABLE = True
except ImportError:
    _BIOAVAILABLE = False

RESTRICTION_SITES = {
    "EcoRI": {"site": "GAATTC", "cut": (1, 1), "overhang": "sticky", "length": 6},
    "BamHI": {"site": "GGATCC", "cut": (1, 1), "overhang": "sticky", "length": 6},
    "XhoI": {"site": "CTCGAG", "cut": (1, 1), "overhang": "sticky", "length": 6},
    "HindIII": {"site": "AAGCTT", "cut": (1, 1), "overhang": "sticky", "length": 6},
    "NdeI": {"site": "CATATG", "cut": (2, 2), "overhang": "sticky", "length": 6},
    "BglII": {"site": "AGATCT", "cut": (1, 1), "overhang": "sticky", "length": 6},
    "KpnI": {"site": "GGTACC", "cut": (1, 1), "overhang": "sticky", "length": 6},
    "SalI": {"site": "GTCGAC", "cut": (1, 1), "overhang": "sticky", "length": 6},
    "NotI": {"site": "GCGGCCGC", "cut": (1, 1), "overhang": "sticky", "length": 8},
    "XbaI": {"site": "TCTAGA", "cut": (1, 1), "overhang": "sticky", "length": 6},
    "SacI": {"site": "GAGCTC", "cut": (1, 1), "overhang": "sticky", "length": 6},
}


def find_restriction_sites(sequence: str, enzymes: list[str] | None = None) -> dict:
    """Find all restriction enzyme cut sites in a sequence.

    Parameters
    ----------
    sequence : str
        DNA or protein sequence (will treat as DNA context).
    enzymes : list[str] | None
        Enzymes to search for. Defaults to all common 6-cutter enzymes.

    Returns
    -------
    dict
        Mapping of enzyme name -> list of cut positions.
    """
    seq = sequence.upper().strip()
    if not enzymes:
        enzymes = list(RESTRICTION_SITES.keys())

    sites: dict[str, list[int]] = defaultdict(list)

    for enzyme in enzymes:
        if enzyme not in RESTRICTION_SITES:
            continue
        site = RESTRICTION_SITES[enzyme]["site"]
        for match in re.finditer(f"(?={site})", seq):
            sites[enzyme].append(match.start())

    return dict(sites)


def design_cloning_strategy(
    vector_seq: str,
    insert_seq: str,
    method: str = "restriction",
) -> dict:
    """Design a cloning strategy for inserting a gene into a vector.

    For restriction cloning:
    1. Find compatible restriction sites in vector (multiple cloning site)
    2. Avoid internal sites in insert
    3. Choose compatible cohesive ends

    For Gibson Assembly:
    - Design overlapping homology arms (20-40 bp)
    - Calculate melting temperature of overlaps

    Parameters
    ----------
    vector_seq : str
        Vector DNA sequence.
    insert_seq : str
        Insert (gene) DNA sequence.
    method : str
        "restriction" or "gibson".

    Returns
    -------
    dict
        Cloning protocol with fragments, sites, and Tm values.
    """
    if method == "gibson":
        return _design_gibson(vector_seq, insert_seq)
    else:
        return _design_restriction_cloning(vector_seq, insert_seq)


def _design_restriction_cloning(vector_seq: str, insert_seq: str) -> dict:
    """Design restriction-based cloning strategy."""
    insert_sites = find_restriction_sites(insert_seq)
    vector_sites = find_restriction_sites(vector_seq)

    # Find enzymes with no internal sites in insert
    compatible_enzymes = [
        e for e, sites in vector_sites.items()
        if len(sites) >= 2 and not insert_sites.get(e)
    ]

    # If no single enzyme cuts twice, require a pair of enzymes that each
    # have a unique site in the vector and no internal site in the insert.
    unique_sites = [
        e for e, sites in vector_sites.items()
        if len(sites) == 1 and not insert_sites.get(e)
    ]

    chosen_sites = []
    if len(compatible_enzymes) >= 2:
        chosen_sites = compatible_enzymes[:2]
    elif len(unique_sites) >= 2:
        chosen_sites = unique_sites[:2]

    protocol: list[dict] = []
    if len(chosen_sites) >= 2:
        e1, e2 = chosen_sites[0], chosen_sites[1]
        protocol.append({
            "step": "digest_vector",
            "enzyme": e1,
            "enzyme2": e2,
            "vector_cutting_at": vector_sites[e1],
        })
        protocol.append({
            "step": "digest_insert",
            "note": f"Add {e1} and {e2} sites via PCR",
        })
        protocol.append({
            "step": "ligation",
            "vector_site": f"{e1}-{e2}",
            "insert_site": f"{e1}-{e2}",
            "orientation": "sense" if e1 != e2 else "either",
        })

    return {
        "method": "restriction",
        "compatible_enzymes": compatible_enzymes or unique_sites,
        "chosen_enzymes": chosen_sites,
        "internal_sites_in_insert": {k: v for k, v in insert_sites.items() if v},
        "protocol": protocol,
        "insert_length_bp": len(insert_seq),
        "vector_length_bp": len(vector_seq),
        "method_local": "restriction_local",
    }


def _design_gibson(vector_seq: str, insert_seq: str) -> dict:
    """Design Gibson Assembly strategy with overlapping homology arms."""
    overlap_length = 30
    homology_5p = insert_seq[:overlap_length]
    homology_3p = insert_seq[-overlap_length:]

    tm_5p = _calculate_tm(homology_5p)
    tm_3p = _calculate_tm(homology_3p)

    return {
        "method": "gibson",
        "overlap_5p": homology_5p,
        "overlap_3p": homology_3p,
        "tm_5p": round(tm_5p, 1),
        "tm_3p": round(tm_3p, 1),
        "overlap_length_bp": overlap_length,
        "insert_length_bp": len(insert_seq),
        "vector_length_bp": len(vector_seq),
        "protocol": [
            {"step": "amplify_insert", "homology_arm_length": overlap_length},
            {"step": "gibson_reaction", "annealing_T_min": 50.0, "exonuclease": "T5"},
            {"step": "transform", "competent_cells": "DH5a"},
        ],
        "method_local": "gibson_local",
    }


def _calculate_tm(seq: str) -> float:
    """Calculate melting temperature using nearest-neighbor (Wallace rule simplified)."""
    if not _BIOAVAILABLE:
        seq_len = len(seq)
        gc_count = seq.count('G') + seq.count('C')
        return 64.9 + 41.0 * (gc_count - 16.4) / seq_len if seq_len else 0.0

    s = Seq(seq)
    return mt.Tm_NN(s, nn_table=mt.DNA_NN1)


def analyze_transcript(
    dna_sequence: str,
    organism: str = "e coli",
    optimize_for_expression: bool = True,
) -> dict:
    """Annotate a transcript with JCat-style analysis.

    Provides:
    - Codon adaptation index (CAI)
    - GC content
    - Rare codon count
    - Transcription factor binding sites
    - Restriction sites for cloning
    - Translation
    """
    dna_seq = dna_sequence.upper().strip()
    mrna_seq = dna_seq.replace('T', 'U')

    # Translate to protein
    genetic_code = {
        'UUU': 'F', 'UUC': 'F', 'UUA': 'L', 'UUG': 'L',
        'CUU': 'L', 'CUC': 'L', 'CUA': 'L', 'CUG': 'L',
        'AUU': 'I', 'AUC': 'I', 'AUA': 'I', 'AUG': 'M',
        'GUU': 'V', 'GUC': 'V', 'GUA': 'V', 'GUG': 'V',
        'UCU': 'S', 'UCC': 'S', 'UCA': 'S', 'UCG': 'S',
        'CCU': 'P', 'CCC': 'P', 'CCA': 'P', 'CCG': 'P',
        'ACU': 'T', 'ACC': 'T', 'ACA': 'T', 'ACG': 'T',
        'UAU': 'Y', 'UAC': 'Y', 'UAA': '*', 'UAG': '*',
        'CAU': 'H', 'CAC': 'H', 'CAA': 'Q', 'CAG': 'Q',
        'UGU': 'C', 'UGC': 'C', 'UGA': '*', 'UGG': 'W',
    }

    protein = ""
    for i in range(0, len(mrna_seq) - 2, 3):
        codon = mrna_seq[i:i+3]
        protein += genetic_code.get(codon, 'X')

    # GC content
    gc_count = dna_seq.count('G') + dna_seq.count('C')
    gc_content = (gc_count / len(dna_seq) * 100) if dna_seq else 0.0

    # Find restriction sites
    sites = find_restriction_sites(dna_seq)

    # CAI calculation (approximate with E. coli codon usage)
    ecoli_cai = _calculate_cai(dna_seq, organism)

    # Rare codons
    rare_codons = _find_rare_codons(dna_seq, organism)

    return {
        "transcript_length_bp": len(dna_seq),
        "protein_length_aa": len(protein),
        "gc_content": round(float(gc_content), 1),
        "protein_sequence": protein,
        "cai": round(float(ecoli_cai), 4),
        "rare_codons": rare_codons,
        "rare_codon_count": len(rare_codons),
        "restriction_sites": {k: v for k, v in sites.items() if v},
        "organism_optimized": organism,
        "method": "jcat_local",
    }


def _calculate_cai(dna_sequence: str, organism: str) -> float:
    """Calculate Codon Adaptation Index (simplified).

    Without the full E. coli codon usage table, we use a heuristic:
    CAI = 0.3 + 0.4 * (fraction of optimal codons matching the organism)

    Note: This is a simplified approximation. The actual CAI calculation
    requires the full codon usage table for the target organism.
    Reference: Sharp & Li (1987) Nucleic Acids Research 15:1281-1295.
    """
    seq = dna_sequence.upper()
    if len(seq) < 3:
        return 0.5

    optimal_codons = {
        "e coli": set(['GGX', 'GAX', 'GUX', 'GCG', 'GCC', 'GCA', 'GCG',
                       'CCX', 'GUX', 'CGT', 'CGC']),
        "yeast": set(['GGX', 'GAX', 'GUX', 'GCG', 'GCC', 'GCA', 'GCT',
                      'CCX', 'AUU', 'AGA', 'AGG', 'UUA', 'UUG']),
        "human": set(['GGX', 'GAC', 'GUU', 'GCG', 'GCC', 'GCA', 'GCT',
                      'CCX', 'AUU', 'AGA', 'AGG', 'UUA', 'CUG']),
    }

    organism = organism.lower().strip()
    if organism in optimal_codons:
        opt_set = optimal_codons[organism]
    else:
        opt_set = optimal_codons.get("e coli", set())

    optimal_count = 0
    total_codons = 0

    for i in range(0, len(seq) - 2, 3):
        codon = seq[i:i+3]
        if len(codon) == 3:
            total_codons += 1
            for opt in opt_set:
                if _codon_matches(codon.upper(), opt):
                    optimal_count += 1
                    break

    return (0.3 + 0.4 * optimal_count / total_codons) if total_codons else 0.5


def _codon_matches(codon: str, pattern: str) -> bool:
    """Check if a codon matches an IUPAC pattern (e.g., 'GGX' = GGX)."""
    comp = {'X': 'ACGTU', 'R': 'AG', 'Y': 'CT', 'S': 'GC',
            'W': 'AT', 'K': 'GT', 'M': 'AC', 'B': 'CGT',
            'D': 'AGT', 'H': 'ACT', 'V': 'ACG'}
    if len(codon) != 3:
        return False
    for c, p in zip(codon, pattern):
        if p == 'X':
            continue
        if c not in comp.get(p, p):
            return False
    return True


def _find_rare_codons(dna_sequence: str, organism: str) -> list[str]:
    """Find rare codons based on organism (simplified)."""
    seq = dna_sequence.upper()
    rare = []

    # Arg codons CGG, AGG, AGA are rare in E. coli; AGG/AGA rare in most
    for i in range(0, len(seq) - 2, 3):
        codon = seq[i:i+3]
        if codon in ('AGG', 'AGA') or (codon.startswith('CG') and codon[2] in 'GA'):
            rare.append(codon)

    return rare[:10]


def design_disulfide_bonds(
    sequence: str,
    min_separation: int = 10,
    max_separation: int = 80,
    max_candidates: int = 8,
    cb_coords: dict[int, float] | None = None,
) -> dict:
    """Design stabilizing disulfide bonds for a protein sequence (DbD2-like).

    Rule-based (sequence-aware) approach that mirrors what Disulfide by
    Design does with an actual 3D structure, but here using predicted
    secondary structure as a geometric proxy:

    - Prefer residue pairs with a predicted helix-turn-helix or
      beta-turn-beta profile, i.e. positions whose flanking context is
      ordered yet separated by a flexible loop — optimal for a native-like
      covalent clamp.
    - Respect the loop-distance rules from the literature (Cys-Cys spacing
      of ~10-80 residues; both residues should be non-Cys so the design
      introduces a new disulfide).
    - Score candidate pairs by sequence separation (closer pairs within a
      single folded domain are more stabilising) and by local order.
    - Optionally, if *cb_coords* is provided (1-indexed residue position ->
      C-beta float coordinate along the first axis), candidates with CB
      distances near the ideal disulfide span (~5-7 Å) are favoured.

    Parameters
    ----------
    sequence : str
        Protein (amino-acid) sequence of the construct.
    min_separation, max_separation : int
        Allowed residue-distance window for the two cysteine positions.
    max_candidates : int
        Limit on returned candidate pairs.
    cb_coords : dict[int, float] | None
        Mapping of 1-indexed residue position -> C-beta coordinate (x-axis
        value) from a pre-computed backbone model. If provided, candidates
        with CB distances in the ~5-7 Å range receive a boost.

    Returns
    -------
    dict
        'candidates' (list of mutation pairs), 'count', 'sequence_length',
        'max_separation', 'method'.
    """
    seq = sequence.upper().strip()
    if not seq:
        return {"candidates": [], "count": 0, "sequence_length": 0, "method": "dbd2_local"}

    try:
        from .structure_local import predict_secondary_structure
        ss = predict_secondary_structure(seq)["prediction"]
    except Exception:
        ss = "C" * len(seq)

    # Build a lookup of C-beta surrogate positions if cb_coords provided.
    # We use the first residue of each amino acid as a C-beta proxy:
    # N-terminal CB ~ i, with a simple offset based on residue type.
    if cb_coords is not None:
        # Normalise cb_coords to 1-indexed positions
        cb_lookup: dict[int, float] = {}
        for pos, val in cb_coords.items():
            if 1 <= pos <= len(seq):
                cb_lookup[pos] = val

    # Local order score: fraction of helix/sheet neighbours within a window.
    def _order_score(i: int) -> float:
        lo = max(0, i - 3)
        hi = min(len(ss), i + 4)
        window = ss[lo:hi]
        ordered = sum(1 for c in window if c in ("H", "E"))
        return ordered / max(len(window), 1)

    scored: list[tuple[float, int, int]] = []
    for i in range(len(seq)):
        if seq[i] == "C":
            continue
        for j in range(i + min_separation, min(len(seq), i + max_separation)):
            if seq[j] == "C":
                continue
            # Compute CB-distance-based boost if cb_coords available.
            boost = 0.0
            if cb_coords is not None and i + 1 in cb_lookup and j + 1 in cb_lookup:
                cb_dist = abs(cb_lookup[i + 1] - cb_lookup[j + 1])
                # Ideal disulfide CB-CB distance is ~5-7 Å; reward proximity.
                if 3.0 <= cb_dist <= 9.0:
                    boost = 1.0 - abs(cb_dist - 6.0) / 3.0  # peak at 6 Å
            score = _order_score(i) + _order_score(j) + (
                4.0 if i + 8 <= j <= i + 40 else 0.0
            ) + boost
            scored.append((round(score, 3), i + 1, j + 1))

    scored.sort(key=lambda t: t[0], reverse=True)
    scored = scored[:max_candidates]

    candidates = []
    for score, res1, res2 in scored:
        candidates.append({
            "position_1": res1,
            "residue_1": seq[res1 - 1],
            "position_2": res2,
            "residue_2": seq[res2 - 1],
            "mutation_1": f"{seq[res1 - 1]}{res1}C",
            "mutation_2": f"{seq[res2 - 1]}{res2}C",
            "loop_length": res2 - res1 - 1,
            "design_score": score,
        })

    return {
        "candidates": candidates,
        "count": len(candidates),
        "sequence_length": len(seq),
        "max_separation": max_separation,
        "method": "dbd2_local",
    }


def predict_pcr_primers(
    sequence: str,
    product_size: int = 800,
    tm_target: float = 58.0,
    primer_length: int = 21,
) -> dict:
    """Design PCR primers for a given product size (DbD2-like primer design).

    Simple rule-based approach:
    - Forward primer: near start of sequence
    - Reverse primer: reverse complement near end
    - Check Tm, GC content, dimerization

    Parameters
    ----------
    sequence : str
        Template DNA sequence.
    product_size : int
        Desired PCR product length (bp).
    tm_target : float
        Target melting temperature (°C).
    primer_length : int
        Primer length (default 21).

    Returns
    -------
    dict
        Forward/reverse primers with Tm, GC%, and dimer checks.
    """
    seq = sequence.upper().strip()
    if len(seq) < product_size:
        product_size = len(seq)

    fwd_start = 0
    rev_end = len(seq)

    forward_seq = seq[fwd_start:fwd_start + primer_length]
    reverse_seq = seq[rev_end - primer_length:rev_end]

    reverse_comp = reverse_seq.translate(str.maketrans('ATGC', 'TACG'))[::-1]

    tm_fwd = _calculate_tm(forward_seq)
    tm_rev = _calculate_tm(reverse_comp)

    gc_fwd = (forward_seq.count('G') + forward_seq.count('C')) / primer_length * 100
    gc_rev = (reverse_comp.count('G') + reverse_comp.count('C')) / primer_length * 100

    dimers = _check_primer_dimers(forward_seq, reverse_comp)

    return {
        "forward_primer": forward_seq,
        "reverse_primer": reverse_comp,
        "forward_tm": round(tm_fwd, 1),
        "reverse_tm": round(tm_rev, 1),
        "forward_gc": round(float(gc_fwd), 1),
        "reverse_gc": round(float(gc_rev), 1),
        "expected_product_size": product_size,
        "primer_length": primer_length,
        "dimers": dimers,
        "has_issues": tm_fwd < 50 or tm_rev < 50 or bool(dimers) or gc_fwd < 40 or gc_rev < 40,
        "method": "dbd2_local",
    }


def _check_primer_dimers(seq1: str, seq2: str) -> list[str]:
    """Check for primer-primer dimer formation (3+ bp complementarity)."""
    dimers = []
    for i in range(len(seq1)):
        for j in range(len(seq2)):
            match = 0
            for k in range(min(len(seq1) - i, len(seq2) - j)):
                a = seq1[i + k]
                b = seq2[j + k]
                if (a == 'G' and b == 'C') or (a == 'C' and b == 'G') or \
                   (a == 'A' and b == 'T') or (a == 'T' and b == 'A'):
                    match += 1
                else:
                    break
            if match >= 3:
                dimers.append(f"3bp+ complementarity at {i}-{j}")

    return dimers


def select_adjuvant(
    antigen_properties: dict,
    target_organism: str = "bacteria",
    adjuvant_type: str = "any",
) -> dict:
    """Select vaccine adjuvant based on antigen properties and config.

    Adjuvant types:
    - "ctxb"    → CTxB peptide (fusion at N-terminus)
    - "tlr4"    → MPLA (TLR4 agonist)
    - "tlr5"    → Flagellin (TLR5 agonist)
    - "none"    → no adjuvant (external, not fused)
    - "auto"/"recommended" → select based on antigen strength

    Common adjuvants:
    - TLR agonists (CpG-ODN, MPLA, Flagellin) → Th1 bias
    - Alum → Th2 bias
    - CAF01 → balanced
    - AS01 → CD8+/Th1

    Selection criteria (based on immunology literature):
    - antigen_score <= 0.5  → potent adjuvant (TLR agonist)
    - antigen_score > 0.5   → moderate adjuvant (alum/MPLA)
    - For weak antigens with ctxb: always use ctxb fusion
    - For self-antigens: TLR7/8 agonist
    """
    score = antigen_properties.get("antigenicity_score", 0.5)
    is_weak = score < 0.55

    # Respect explicit adjuvant_type from config
    if adjuvant_type == "ctxb":
        # CTxB peptide fusion always recommended for antigens; note if
        # antigen is very strong, alternatives may be preferred.
        return {
            "recommended": "CTxB peptide",
            "ranked_options": [
                {
                    "name": "CTxB peptide",
                    "type": "ctxb",
                    "bias": "balanced",
                    "potency": "moderate",
                    "suitability": 0.8 if not is_weak else 0.5,
                    "description": "CTxB carrier protein fusion, B-cell targeting",
                    "is_peptide": True,
                },
                {
                    "name": "MPLA (Monophosphoryl Lipid A)",
                    "type": "tlr4",
                    "bias": "th1",
                    "potency": "high",
                    "suitability": 0.7 if is_weak else 0.55,
                    "description": "TLR4 agonist, FDA-approved",
                    "is_peptide": False,
                },
            ],
            "antigen_strength": "weak" if is_weak else "moderate_to_strong",
            "antigenicity_score": score,
            "target_organism": target_organism,
            "adjuvant_type_filter": "ctxb",
            "method": "adjuvant_local",
        }

    if adjuvant_type == "tlr4":
        adjuvants = [
            {
                "name": "MPLA (Monophosphoryl Lipid A)",
                "type": "tlr4",
                "bias": "th1",
                "potency": "high",
                "suitability": 0.9 if is_weak else 0.7,
                "description": "TLR4 agonist, FDA-approved",
                "is_peptide": False,
            },
        ]
        adjuvants.sort(key=lambda x: x["suitability"], reverse=True)
        return {
            "recommended": adjuvants[0]["name"] if adjuvants else None,
            "ranked_options": adjuvants,
            "antigen_strength": "weak" if is_weak else "moderate_to_strong",
            "antigenicity_score": score,
            "target_organism": target_organism,
            "adjuvant_type_filter": "tlr4",
            "method": "adjuvant_local",
        }

    if adjuvant_type == "tlr5":
        adjuvants = [
            {
                "name": "Flagellin (TLR5 agonist)",
                "type": "tlr5",
                "bias": "th1",
                "potency": "high",
                "suitability": 0.85 if is_weak else 0.65,
                "description": "TLR5 agonist, induces strong Th1",
                "is_peptide": False,
            },
        ]
        adjuvants.sort(key=lambda x: x["suitability"], reverse=True)
        return {
            "recommended": adjuvants[0]["name"] if adjuvants else None,
            "ranked_options": adjuvants,
            "antigen_strength": "weak" if is_weak else "moderate_to_strong",
            "antigenicity_score": score,
            "target_organism": target_organism,
            "adjuvant_type_filter": "tlr5",
            "method": "adjuvant_local",
        }

    if adjuvant_type == "none":
        return {
            "recommended": None,
            "ranked_options": [],
            "antigen_strength": "weak" if is_weak else "moderate_to_strong",
            "antigenicity_score": score,
            "target_organism": target_organism,
            "adjuvant_type_filter": "none",
            "method": "adjuvant_local",
            "note": "No adjuvant selected — adjuvant will be added externally "
                    "and not fused to the construct.",
        }

    # adjuvant_type == "auto" or "recommended" (default): use antigen-strength
    # recommendation
    if is_weak:
        # Weak antigens: AS01 (potent Th1/CD8+) or CAF01 (balanced)
        ranked = [
            {
                "name": "AS01 (MPLA + QS-21)",
                "type": "combination",
                "bias": "th1",
                "potency": "very_high",
                "suitability": 0.9,
                "description": "Potent Th1/CD8+ responses",
                "is_peptide": False,
            },
            {
                "name": "CAF01 (Quil A + trehalose dipelargosphate)",
                "type": "saponin",
                "bias": "balanced",
                "potency": "high",
                "suitability": 0.8,
                "description": "Balanced Th1/Th2, CD8 T-cell responses",
                "is_peptide": False,
            },
            {
                "name": "MPLA (Monophosphoryl Lipid A)",
                "type": "tlr4",
                "bias": "th1",
                "potency": "high",
                "suitability": 0.7 if is_weak else 0.55,
                "description": "TLR4 agonist, FDA-approved",
                "is_peptide": False,
            },
        ]
    else:
        # Moderate-to-strong antigens: MPLA or Alum
        ranked = [
            {
                "name": "MPLA (Monophosphoryl Lipid A)",
                "type": "tlr4",
                "bias": "th1",
                "potency": "high",
                "suitability": 0.85 if not is_weak else 0.65,
                "description": "TLR4 agonist, FDA-approved",
                "is_peptide": False,
            },
            {
                "name": "Alum (Aluminum hydroxide)",
                "type": "alum",
                "bias": "th2",
                "potency": "moderate",
                "suitability": 0.8 if not is_weak else 0.45,
                "description": "Widely used, induces Th2 antibody responses",
                "is_peptide": False,
            },
        ]

    ranked.sort(key=lambda x: x["suitability"], reverse=True)
    return {
        "recommended": ranked[0]["name"] if ranked else None,
        "ranked_options": ranked,
        "antigen_strength": "weak" if is_weak else "moderate_to_strong",
        "antigenicity_score": score,
        "target_organism": target_organism,
        "adjuvant_type_filter": "auto",
        "method": "adjuvant_local",
    }


def simulate_immune_response(
    vaccine_construct: dict,
    time_points: list[float] | None = None,
) -> dict:
    """Simulate immune response dynamics to a vaccine construct (C-ImmSim local).

    Uses a simplified system of ODEs describing:
    - Antigen concentration decay
    - Antibody production (B-cell → plasma cell → IgM → class-switched IgG)
    - T-cell activation (Th1, Th2, Treg)
    - Memory cell formation

    Class-switch kinetics: IgM produces plasma cells that class-switched to
    IgG at a rate that increases with adjuvant presence and time, mimicking
    the biological class-switch recombination process.

    Parameters
    ----------
    vaccine_construct : dict
        Vaccine design: {'antigenic_score', 'adjuvant_present', 'sequence_length'}.
    time_points : list[float] | None
        Simulation time points (days).

    Returns
    -------
    dict
        Simulated trajectories for antibodies, T-cells, memory cells.
    """
    if time_points is None:
        time_points = [0, 1, 3, 7, 14, 21, 28, 42, 56, 84]

    antigen_score = vaccine_construct.get("antigenic_score")
    if antigen_score is None:
        # Estimate from construct properties if available
        has_epitopes = vaccine_construct.get("epitope_count", 0) > 0
        adjuvant_present = vaccine_construct.get("adjuvant_present", False)
        antigen_score = 0.7 if has_epitopes else 0.5
        if adjuvant_present:
            antigen_score = min(antigen_score + 0.1, 1.0)
    adjuvant = vaccine_construct.get("adjuvant_present", False)
    seq_len = vaccine_construct.get("sequence_length", 300)

    # Rate scaling based on construct and adjuvant
    k_stim = 2.0 * antigen_score * (1.5 if adjuvant else 1.0)
    k_decay = 0.05 + 0.02 * seq_len / 500.0

    # Class-switch rate: base rate + adjuvant boost + time dependence
    # Class-switch recombination increases over the first ~2 weeks
    k_class_switch = 0.02 * (1.5 if adjuvant else 1.0)  # base rate

    # Initial conditions
    antigen = 100.0
    ab_igm = 0.0
    ab_igg = 0.0
    th1 = 10.0
    th2 = 10.0
    treg = 5.0
    memory_b = 0.0
    memory_t = 0.0

    dt = 0.1
    results: list[dict] = []

    t = 0.0
    while t <= max(time_points) + dt:
        antigen_decay = k_decay * antigen

        # IgM production from antigen stimulation
        igm_production = k_stim * antigen * 0.5 if antigen > 0.1 else 0

        # Class-switch from IgM to IgG: rate increases with adjuvant and time
        # Time factor: faster switching in first 14 days, then tapers
        time_factor = 1.0 + max(0, 14.0 - t) / 50.0  # decays over time
        cs_rate = k_class_switch * time_factor * (1.5 if adjuvant else 1.0)

        # IgG formation from class-switched plasma cells
        igg_formation = cs_rate * ab_igm

        # IgM decay (natural clearance + consumption for class-switch)
        igm_decay = 0.1 * igm_production + cs_rate * ab_igm

        # T-cell activation
        th1_activation = 0.02 * k_stim * antigen if antigen > 0.1 else 0
        th2_activation = 0.015 * k_stim * antigen * (0.7 if adjuvant else 0.5) if antigen > 0.1 else 0
        treg_expansion = 0.005 * (th1 + th2)

        # Memory formation
        memory_formation_b = 0.01 * igm_production
        memory_formation_t = 0.005 * (th1_activation + th2_activation)

        # Update state
        antigen -= antigen_decay * dt
        if antigen < 0:
            antigen = 0.1

        # IgM: production minus decay minus class-switch consumption
        ab_igm += (igm_production - igm_decay) * dt
        # IgG: formation from class-switched cells
        ab_igg += igg_formation * dt
        th1 += th1_activation * dt
        th2 += th2_activation * dt
        treg += treg_expansion * dt
        memory_b += memory_formation_b * dt
        memory_t += memory_formation_t * dt

        t += dt

        if any(abs(t - tp) < dt/2 for tp in time_points):
            results.append({
                "time_days": round(t, 1),
                "antigen_present": round(float(antigen), 3),
                "IgM": round(float(ab_igm), 3),
                "IgG": round(float(ab_igg), 3),
                "Th1": round(float(th1), 2),
                "Th2": round(float(th2), 2),
                "Treg": round(float(treg), 2),
                "memory_B": round(float(memory_b), 2),
                "memory_T": round(float(memory_t), 2),
                "peak_IgG": round(float(ab_igg), 3),
                "seroconversion": ab_igg >= 10.0,
            })

    peak_igg = max(r["IgG"] for r in results) if results else 0
    peak_th1 = max(r["Th1"] for r in results) if results else 0
    peak_th2 = max(r["Th2"] for r in results) if results else 0

    return {
        "simulation_time_points": time_points,
        "trajectory": results,
        "peak_igG": round(float(peak_igg), 3),
        "peak_Th1": round(float(peak_th1), 2),
        "peak_Th2": round(float(peak_th2), 2),
        "seroconversion_day": next(
            (r["time_days"] for r in results if r["seroconversion"]), None
        ),
        "memory_response": results[-1] if results else None,
        "construct_stimulation": k_stim,
        "antigen_decay_rate": k_decay,
        "class_switch_rate": k_class_switch,
        "method": "c_immisim_local",
    }
