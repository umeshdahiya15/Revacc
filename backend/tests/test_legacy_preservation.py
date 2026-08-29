"""Baseline preservation tests for legacy B-cell, MEV, and single-run paths.

These tests intentionally exercise the unfixed implementation and capture
contracts that later fixes must preserve. All inputs are deterministic/local.
"""
from __future__ import annotations

from typing import Any

import pytest
from hypothesis import given, strategies as st

from app.models import Epitope, JobCreate
from app.repo import repo
from app.routes import get_job, job_epitopes, list_jobs
from app.tools import bcell_local, runner as runner_mod
from app.tools.runner_additions import (
    CTXB_ADJUSTANT,
    LINKER_AAY,
    LINKER_EAAAK,
    LINKER_GPGPG,
    LINKER_KK,
    _assemble_mev_construct,
)


def _epitopes() -> list[dict[str, Any]]:
    """Return the deterministic legacy 5/3/6 fixture used by the MEV path."""
    ctl = ["ACDEFGHIK", "CDEFGHIKL", "DEFGHIKLM", "EFGHIKLMN", "FGHIKLMNP"]
    htl = ["KLMNPQRSTVWYACD", "LMNPQRSTVWYACDE", "MNPQRSTVWYACDEF"]
    bcell = [
        "RSTVWYACDEFGHIK",
        "STVWYACDEFGHIKL",
        "TVWYACDEFGHIKLM",
        "VWYACDEFGHIKLMN",
        "WYACDEFGHIKLMNP",
        "YACDEFGHIKLMNPQRSTVW",
    ]
    rows: list[dict[str, Any]] = []
    for i, sequence in enumerate(ctl):
        rows.append({"id": f"ctl-{i}", "type": "CTL", "sequence": sequence,
                     "sourceProtein": f"protein-{i:02d}", "sourceProteinId": f"ctl-{i}",
                     "hlaAllele": "HLA-A*02:01", "percentileRank": 0.1 + i,
                     "start": i, "selected": True})
    for i, sequence in enumerate(htl):
        rows.append({"id": f"htl-{i}", "type": "HTL", "sequence": sequence,
                     "sourceProtein": f"protein-{i:02d}", "sourceProteinId": f"htl-{i}",
                     "hlaAllele": "HLA-DRB1*01:01", "percentileRank": 0.1 + i,
                     "start": i, "selected": True})
    for i, sequence in enumerate(bcell):
        rows.append({"id": f"bcell-{i}", "type": "BCELL_LINEAR", "sequence": sequence,
                     "sourceProtein": f"protein-{i:02d}", "sourceProteinId": f"b-{i}",
                     "antigenicityScore": 0.1 + i, "start": i, "selected": True})
    return rows


def _legacy_result() -> dict[str, Any]:
    return _assemble_mev_construct(_epitopes(), LINKER_EAAAK, LINKER_GPGPG, LINKER_KK)


def test_legacy_mev_sequence_length_and_metrics_are_preserved() -> None:
    """The option-disabled baseline uses paper-aligned caps (8/8/5).

    The fixture has 5 CTL, 3 HTL, and 6 B-cell epitopes.  With paper-aligned
    caps CTL_CAP=8, HTL_CAP=8, BCELL_CAP=5 the result is 5 CTL + 3 HTL + 5 B-cell.
    B-cell epitopes are sorted by antigenicityScore (desc) before capping.

    **Validates: Requirements 3.7**
    """
    result = _legacy_result()
    expected_sequence = (
        CTXB_ADJUSTANT + LINKER_EAAAK + LINKER_AAY
        + "ACDEFGHIK" + LINKER_AAY + "CDEFGHIKL" + LINKER_AAY
        + "DEFGHIKLM" + LINKER_AAY + "EFGHIKLMN" + LINKER_AAY + "FGHIKLMNP"
        + LINKER_GPGPG + "KLMNPQRSTVWYACD" + LINKER_GPGPG + "LMNPQRSTVWYACDE"
        + LINKER_GPGPG + "MNPQRSTVWYACDEF"
        + LINKER_KK + "STVWYACDEFGHIKL" + LINKER_KK + "TVWYACDEFGHIKLM"
        + LINKER_KK + "VWYACDEFGHIKLMN" + LINKER_KK + "WYACDEFGHIKLMNP"
        + LINKER_KK + "YACDEFGHIKLMNPQRSTVW"
    )
    assert result == {
        "sequence": expected_sequence,
        "length": len(expected_sequence),
        "adjuvant": CTXB_ADJUSTANT,
        "adjuvantSource": "UniProt P01556 (CTxB), configured legacy sequence",
        "signalPeptide": None,
        "signalPeptideSource": None,
        "linker_ctr": LINKER_EAAAK,
        "linker_htl": LINKER_GPGPG,
        "linker_bcell": LINKER_KK,
        "ctl_epitopes": 5,
        "htl_epitopes": 3,
        "bcell_epitopes": 5,
    }


@given(order=st.permutations(_epitopes()))
def test_legacy_mev_identity_is_independent_of_input_order(order: list[dict[str, Any]]) -> None:
    """Legacy assembly remains deterministic for arbitrary input ordering.

    **Validates: Requirements 3.7**
    """
    assert _assemble_mev_construct(order, LINKER_EAAAK, LINKER_GPGPG, LINKER_KK) == _legacy_result()


@given(sequence=st.text(alphabet="ACDEFGHIKLMNPQRSTVWY", max_size=80))
def test_bcell_default_is_identical_to_explicit_legacy_window_seven(sequence: str) -> None:
    """Default BepiPred behavior is exactly explicit window 7 at threshold 0.5.

    **Validates: Requirements 3.6**
    """
    default = bcell_local.predict_bepipred_epitope(sequence)
    explicit = bcell_local.predict_bepipred_epitope(sequence, 7)
    assert default == explicit
    assert default["window_size"] == 7
    assert default["threshold"] == 0.5


def _reset_repo() -> None:
    repo._jobs.clear()
    repo._events.clear()
    runner_mod._RUN_SESSION.clear()


def test_repo_and_single_run_routes_preserve_list_get_and_epitope_behavior() -> None:
    """List/get and single-job epitope routes retain their existing contracts.

    **Validates: Requirements 3.9**
    """
    _reset_repo()
    job = repo.create(JobCreate(name="legacy single-run", pathogenName="fixture"), job_id="job-legacy")
    job.epitopes = [
        Epitope(id="ctl-1", type="CTL", sequence="ACDEFGHIK", sourceProtein="protein-a", selected=True),
        Epitope(id="htl-1", type="HTL", sequence="KLMNPQRSTVWYACD", sourceProtein="protein-b", selected=True),
    ]
    repo.upsert(job)

    listed = list_jobs()
    fetched = get_job("job-legacy")
    all_epitopes = job_epitopes("job-legacy")
    ctl_epitopes = job_epitopes("job-legacy", type="CTL")

    assert [item.id for item in listed] == ["job-legacy"]
    assert fetched.id == job.id
    assert [item.id for item in all_epitopes] == ["ctl-1", "htl-1"]
    assert [item.id for item in ctl_epitopes] == ["ctl-1"]
    assert repo.get("missing-job") is None


def test_single_run_routes_keep_not_found_contract() -> None:
    """Unknown single-run resources continue to return HTTP 404 errors.

    **Validates: Requirements 3.9**
    """
    from fastapi import HTTPException

    _reset_repo()
    for lookup in (lambda: get_job("missing"), lambda: job_epitopes("missing")):
        with pytest.raises(HTTPException) as exc_info:
            lookup()
        assert exc_info.value.status_code == 404
