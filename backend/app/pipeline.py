"""Canonical 14-phase / 41-step pipeline definition.

Mirrors `PIPELINE_PHASES` in the frontend `src/lib/constants.ts`.
"""
from __future__ import annotations

import re
from typing import Iterator

PHASES: list[tuple[str, list[tuple[str, str]]]] = [
    ("Proteome Retrieval & Redundancy Removal", [("Retrieve Proteome", "UniProt"), ("Remove Redundant Sequences", "CD-HIT")]),
    ("Subtractive Proteomics Filtering", [
        ("Identify Essential Proteins", "BLASTp + DEG"),
        ("Subcellular Localization", "PSORTb"),
        ("Transmembrane Helix Prediction", "DeepTMHMM"),
        ("Signal Peptide Prediction", "Phobius"),
    ]),
    ("Antigenicity & Safety Screening", [
        ("Allergenicity Check", "AlgPred 2.0"),
        ("Antigenicity Prediction", "VaxiJen"),
        ("Virulence Factor Check", "BLASTp + VFDB"),
        ("Human Homology Check", "BLASTp + Human Proteome"),
    ]),
    ("Structural Prediction & Validation", [
        ("Physicochemical Properties", "ProtParam"),
        ("3D Structure Prediction", "Structure Provider"),
        ("Structure Quality Validation", "Local coordinate quality analysis"),
        ("Secondary Structure Prediction", "SOPMA"),
    ]),
    ("CTL (CD8+ T-Cell) Epitope Prediction & Filtering", [
        ("Predict CTL Epitopes", "IEDB MHC-I"),
        ("CTL Antigenicity", "VaxiJen"),
        ("CTL Allergenicity", "AlgPred 2.0"),
        ("CTL Toxicity", "ToxinPred"),
        ("CTL Immunogenicity", "Local analysis (IEDB inputs)"),
    ]),
    ("HTL (CD4+ T-Cell) Epitope Prediction & Filtering", [
        ("Predict HTL Epitopes", "IEDB MHC-II"),
        ("IFN-γ Induction", "IFNepitope"),
        ("IL-4 Induction", "IL4Pred"),
        ("IL-10 Induction", "IL10Pred"),
        ("HTL Antigenicity", "VaxiJen"),
        ("HTL Allergenicity", "AlgPred 2.0"),
        ("HTL Toxicity", "ToxinPred"),
    ]),
    ("B-Cell Epitope Prediction & Filtering", [
        ("Linear B-Cell Epitopes", "ABCpred"),
        ("B-Cell Antigenicity", "VaxiJen"),
        ("B-Cell Allergenicity", "AlgPred 2.0"),
        ("B-Cell Toxicity", "ToxinPred"),
        ("Conformational B-Cell Epitopes", "Ellipro"),
    ]),
    ("Population Coverage & Epitope Overlap", [
        ("Population Coverage Analysis", "IEDB-AR"),
        ("CTL-HTL Epitope Overlap Analysis", "Local sequence overlap"),
    ]),
    ("MEV Construct Assembly", [("Select Adjuvant", "Library"), ("Assemble MEV Construct", "BioPython")]),
    ("MEV Construct Validation", [
        ("Physicochemical Properties", "ProtParam"),
        ("Antigenicity", "VaxiJen"),
        ("Allergenicity", "AlgPred 2.0"),
        ("Toxicity", "ToxinPred"),
        ("Solubility", "Protein-Sol"),
    ]),
    ("MEV 3D Structure & Validation", [
        ("Secondary Structure", "SOPMA"),
        ("3D Structure Prediction", "AlphaFold/SwissModel"),
        ("Ramachandran Plot", "MolProbity"),
        ("ERRAT Validation", "ERRAT"),
        ("ProSA-web Validation", "ProSA-web"),
    ]),
    ("Disulfide Bond Engineering", [("Design Disulfide Bonds", "DbD2")]),
    ("Codon Optimization & In Silico Cloning", [
        ("Codon Optimization", "JCat"),
        ("Restriction Site Analysis", "BioPython"),
        ("In Silico Cloning", "Benchling"),
    ]),
    ("Immune Simulation", [("Immune Response Simulation", "C-ImmSim")]),
]

TOTAL_STEPS = sum(len(steps) for _, steps in PHASES)


def step_id(phase: int, number: int) -> str:
    return f"{phase}-{number}"


def iter_phase_steps() -> Iterator[tuple[int, str, int, str, str]]:
    """Yield (phase_number, phase_name, step_number, step_name, tool)."""
    for phase_no, (phase_name, steps) in enumerate(PHASES, start=1):
        for step_no, (step_name, tool) in enumerate(steps, start=1):
            yield phase_no, phase_name, step_no, step_name, tool


def slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
