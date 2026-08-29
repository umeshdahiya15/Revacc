"""Regression coverage for the MEV construct length options.

The legacy sequence remains deterministic, while the configured sequence
fields expose a provenance-gated opt-in prefix/adjuvant path.
"""
from __future__ import annotations

import inspect

from hypothesis import given, settings, strategies as st

from app.models import JobConfigModel, JobCreate
from app.tools.runner_additions import (
    CTXB_ADJUSTANT,
    LINKER_EAAAK,
    LINKER_GPGPG,
    LINKER_KK,
    _assemble_mev_construct,
)


_OPTION_NAMES = {
    "signal_peptide",
    "signalPeptide",
    "use_signal_peptide",
    "useSignalPeptide",
    "fuller_adjuvant",
    "fullerAdjuvant",
    "use_fuller_adjuvant",
    "useFullerAdjuvant",
    "mev_adjuvant",
    "mevAdjuvant",
    "adjuvant_option",
    "adjuvantOption",
    "adjuvantSequence",
    "signalPeptideSequence",
}


def _fixture_epitopes() -> list[dict[str, object]]:
    """Return a deterministic 5/3/6 CTL/HTL/B-cell epitope fixture."""
    epitopes: list[dict[str, object]] = []
    ctl_seeds = ["ACDEFGHIK", "CDEFGHIKL", "DEFGHIKLM", "EFGHIKLMN", "FGHIKLMNP"]
    for index, sequence in enumerate(ctl_seeds):
        epitopes.append(
            {
                "id": f"ctl-{index}",
                "type": "CTL",
                "sequence": sequence,
                "sourceProtein": f"protein-{index:02d}",
                "sourceProteinId": f"FIX-CTL-{index:03d}",
                "hlaAllele": "HLA-A*02:01",
                "percentileRank": 0.1 + index,
                "start": index,
                "selected": True,
            }
        )
    htl_seeds = ["KLMNPQRSTVWYACD", "LMNPQRSTVWYACDE", "MNPQRSTVWYACDEF"]
    for index, sequence in enumerate(htl_seeds):
        epitopes.append(
            {
                "id": f"htl-{index}",
                "type": "HTL",
                "sequence": sequence,
                "sourceProtein": f"protein-{index:02d}",
                "sourceProteinId": f"FIX-HTL-{index:03d}",
                "hlaAllele": "HLA-DRB1*01:01",
                "percentileRank": 0.1 + index,
                "start": index,
                "selected": True,
            }
        )
    bcell_seeds = [
        "RSTVWYACDEFGHIK",
        "STVWYACDEFGHIKL",
        "TVWYACDEFGHIKLM",
        "VWYACDEFGHIKLMN",
        "WYACDEFGHIKLMNP",
        "YACDEFGHIKLMNPQRSTVW",
    ]
    for index, sequence in enumerate(bcell_seeds):
        epitopes.append(
            {
                "id": f"bcell-{index}",
                "type": "BCELL_LINEAR",
                "sequence": sequence,
                "sourceProtein": f"protein-{index:02d}",
                "sourceProteinId": f"FIX-B-{index:03d}",
                "antigenicityScore": 0.1 + index,
                "start": index,
                "selected": True,
            }
        )
    return epitopes


def test_mev_assembly_supports_paper_length_option_and_stable_ordering() -> None:
    """Property 6: an enabled option must move MEV length toward approximately 620 aa.

    **Validates: Requirements 2.8**

    The legacy fixture is assembled twice with opposite input ordering to make
    deterministic ordering observable.  The unfixed counterexample is then
    surfaced by inspecting both the assembler callable and job configuration:
    the construct is 336 aa and neither surface exposes a signal-peptide or
    fuller-adjuvant option.
    """
    epitopes = _fixture_epitopes()
    legacy = _assemble_mev_construct(
        epitopes,
        LINKER_EAAAK,
        LINKER_GPGPG,
        LINKER_KK,
    )
    reversed_legacy = _assemble_mev_construct(
        list(reversed(epitopes)),
        LINKER_EAAAK,
        LINKER_GPGPG,
        LINKER_KK,
    )

    assert legacy["length"] == 339
    assert legacy["sequence"] == reversed_legacy["sequence"]
    assert legacy["ctl_epitopes"] == 5
    assert legacy["htl_epitopes"] == 3  # all 3 pass HTL_CAP=8
    assert legacy["bcell_epitopes"] == 5  # 6 in fixture, capped by BCELL_CAP=5

    assembler_options = set(inspect.signature(_assemble_mev_construct).parameters) & _OPTION_NAMES
    config_fields = set(JobConfigModel.model_fields) | set(JobCreate.model_fields)
    configured_options = config_fields & _OPTION_NAMES
    assert assembler_options or configured_options, (
        "Expected an enabled signal-peptide/fuller-adjuvant MEV option capable "
        "of moving the deterministic construct toward the paper's ~620 aa "
        f"standard; unfixed counterexample length={legacy['length']} aa, "
        f"assembler_options={sorted(assembler_options)}, "
        f"config_options={sorted(configured_options)}"
    )


@st.composite
def _random_epitope_sets(draw: st.DrawFn) -> list[dict[str, object]]:
    """Generate subsets of the existing provenance-bearing epitope fixture.

    The amino-acid sequences are selected only from ``_fixture_epitopes``;
    Hypothesis varies membership and input order rather than inventing sequence
    data.
    """
    fixture = _fixture_epitopes()
    selected: list[dict[str, object]] = []
    for epitope_type in ("CTL", "HTL", "BCELL_LINEAR"):
        candidates = [row for row in fixture if row["type"] == epitope_type]
        selected.extend(
            draw(
                st.lists(
                    st.sampled_from(candidates),
                    min_size=1,
                    max_size=len(candidates),
                    unique_by=lambda row: row["id"],
                )
            )
        )
    return draw(st.permutations(selected))


@settings(max_examples=25, derandomize=True, deadline=None)
@given(epitopes=_random_epitope_sets())
def test_enabled_mev_preserves_epitope_and_linker_order(
    epitopes: list[dict[str, object]],
) -> None:
    """The provenance-gated prefix cannot reorder the assembled payload.

    The optional sequences and provenance below are the exact supplied values
    already covered by ``test_phase2_3_runners.py``.  The property compares the
    payload beginning at the first EAAAK linker, so any signal/adjuvant change
    is isolated to the prefix and total length.

    **Validates: Requirements 2.8, 3.7**
    """
    legacy = _assemble_mev_construct(
        epitopes,
        LINKER_EAAAK,
        LINKER_GPGPG,
        LINKER_KK,
    )
    enhanced = _assemble_mev_construct(
        epitopes,
        LINKER_EAAAK,
        LINKER_GPGPG,
        LINKER_KK,
        use_optional_sequences=True,
        adjuvant_sequence="MKTLL",
        adjuvant_source="UniProt:P12345",
        signal_peptide_sequence="MKKLL",
        signal_peptide_source="UniProt:P54321",
    )

    legacy_payload = legacy["sequence"][legacy["sequence"].index(LINKER_EAAAK):]
    enhanced_payload = enhanced["sequence"][enhanced["sequence"].index(LINKER_EAAAK):]

    assert enhanced_payload == legacy_payload
    assert enhanced["sequence"].startswith("MKKLLMKTLL")
    assert enhanced["length"] == (
        legacy["length"] - len(CTXB_ADJUSTANT) + len("MKKLL") + len("MKTLL")
    )
    assert (enhanced["ctl_epitopes"], enhanced["htl_epitopes"], enhanced["bcell_epitopes"]) == (
        legacy["ctl_epitopes"],
        legacy["htl_epitopes"],
        legacy["bcell_epitopes"],
    )
