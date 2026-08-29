"""Focused contracts for DEG reference selection and UID output shape."""
from __future__ import annotations

from app.tools import deg


def test_default_scope_selects_complete_deg10_reference() -> None:
    assert deg.normalize_scope("all") == deg.DEG10_SCOPE
    assert deg.normalize_scope("deg10") == deg.DEG10_SCOPE
    assert deg.organism_for_scope("all", "Streptococcus agalactiae") is None
    assert deg.reference_label_for_scope("all", None) == (
        "DEG 10 (complete bacterial essential-protein set)"
    )


def test_species_scope_remains_explicit_and_uid_shape_is_unchanged() -> None:
    organism = deg.organism_for_scope("species", "Streptococcus agalactiae")
    assert organism == "Streptococcus agalactiae"
    assert deg.normalize_scope("species") == "species"

    genes = [
        deg.EssentialGene(
            deg_entry="DEG1042",
            gene_id="DEG10420001",
            gi=446060347,
            locus_tag="SAK_0001",
            product="fixture essential protein",
            organism=organism,
        )
    ]
    assert deg.essential_uids(genes) == [446060347]
    assert all(isinstance(uid, int) for uid in deg.essential_uids(genes))
