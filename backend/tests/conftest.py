"""Deterministic fixtures shared by pipeline-fixes tests.

The fixtures are opt-in: requesting one patches only the test's lifetime, so
legacy unittest suites and live application code keep their existing behavior.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from app.tools import iedb, psortb_docker, runner_additions


MHCI_TSV = (
    "allele\tseq_num\tstart\tend\tlength\tpeptide\tcore\tscore\tpercentile_rank\n"
    "HLA-A*02:01\t1\t1\t9\t9\tSVPNKLSYL\tSVPNKLSYL\t0.82\t0.06\n"
    "HLA-A*02:01\t2\t1\t9\t9\tYTFATVAPV\tYTFATVAPV\t0.78\t0.12\n"
)
MHCII_TSV = (
    "allele\tseq_num\tstart\tend\tlength\tcore_peptide\tpeptide\tscore\trank\n"
    "HLA-DRB1*01:01\t1\t1\t15\t15\tAHKVPRRLL\tGHAHKVPRRLLKAAR\t0.9\t0.4\n"
)


@dataclass
class MockIEDBResponse:
    text: str
    status_code: int = 200

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"mock IEDB status {self.status_code}")


class MockIEDBPost:
    """Callable replacement for ``iedb._post`` with request accounting."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.responses = {"mhci": MHCI_TSV, "mhcii": MHCII_TSV}
        self.error: Exception | None = None

    async def __call__(self, _client: Any, url: str, data: dict[str, str]) -> MockIEDBResponse:
        self.calls.append({"url": url, "data": data.copy()})
        if self.error is not None:
            raise self.error
        key = "mhcii" if "mhcii" in url else "mhci"
        return MockIEDBResponse(self.responses[key])


@pytest.fixture
def mock_iedb_post(monkeypatch: pytest.MonkeyPatch) -> MockIEDBPost:
    """Patch IEDB HTTP POSTs with deterministic TSV responses and call tracking."""
    post = MockIEDBPost()
    monkeypatch.setattr(iedb, "_post", post)
    return post


@pytest.fixture
def fixture_proteome() -> list[dict[str, Any]]:
    """Small proteome covering PSORTb surface categories and intracellular input."""
    return [
        {
            "index": 0,
            "uniprotId": "FIX-OM-001",
            "name": "Outer membrane protein",
            "sequence": "M" + "A" * 59,
            "expected_localization": "OuterMembrane",
        },
        {
            "index": 1,
            "uniprotId": "FIX-LIPO-001",
            "name": "Lipoprotein",
            "sequence": "M" + "C" * 59,
            "expected_localization": "Lipoprotein",
        },
        {
            "index": 2,
            "uniprotId": "FIX-WALL-001",
            "name": "Wall anchored protein",
            "sequence": "M" + "G" * 59,
            "expected_localization": "CellWall",
        },
        {
            "index": 3,
            "uniprotId": "FIX-INTRA-001",
            "name": "Intracellular protein",
            "sequence": "M" + "K" * 59,
            "expected_localization": "Cytoplasmic",
        },
        {
            "index": 4,
            "uniprotId": "FIX-SECRETED-001",
            "name": "Secreted protein",
            "sequence": "M" + "S" * 59,
            "expected_localization": "Extracellular",
        },
    ]


@pytest.fixture
def fixture_epitopes() -> dict[str, list[iedb.EpitopePrediction]]:
    """Deterministic MHC-I/MHC-II rows for downstream runner tests."""
    return {
        "mhci": iedb._parse_tsv(MHCI_TSV, has_rank=False),
        "mhcii": iedb._parse_tsv(MHCII_TSV, has_rank=True),
    }


@pytest.fixture
def mock_ebi_psortb(
    monkeypatch: pytest.MonkeyPatch,
    fixture_proteome: list[dict[str, Any]],
) -> dict[str, Any]:
    """Patch EBI Phobius and PSORTb adapters with offline deterministic results.

    ``phobius_by_sequence`` can be replaced by a test to exercise specific
    signal-peptide/TM classifications. PSORTb results are derived from the
    fixture's explicit expected localization labels.
    """
    state: dict[str, Any] = {
        "submissions": [],
        "jobs": {},
        "phobius_by_sequence": {},
    }
    localization_by_sequence = {
        protein["sequence"]: protein["expected_localization"]
        for protein in fixture_proteome
    }

    class MockEBIClient:
        def __init__(self, **_kwargs: Any) -> None:
            pass

        async def submit(self, tool: str, sequence: str) -> str:
            job_id = f"{tool}-mock-{len(state['submissions']) + 1}"
            state["submissions"].append({"tool": tool, "sequence": sequence})
            state["jobs"][job_id] = sequence
            return job_id

        async def poll_status(self, _tool: str, _job_id: str) -> str:
            return "FINISHED"

        async def fetch_result(self, _tool: str, job_id: str, _result_type: str = "out") -> str:
            sequence = state["jobs"][job_id]
            return state["phobius_by_sequence"].get(sequence, "ID MOCK\n//")

        async def close(self) -> None:
            return None

    async def mock_psortb_localize(
        sequences: list[str], *, gram: str = "positive"
    ) -> list[dict[str, Any]]:
        del gram
        return [
            {
                "localization": localization_by_sequence.get(sequence, "Unknown"),
                "score": 1.0,
                "cached": False,
            }
            for sequence in sequences
        ]

    monkeypatch.setattr(runner_additions, "EBIRestClient", MockEBIClient)
    monkeypatch.setattr(psortb_docker, "require_available", lambda: None)
    monkeypatch.setattr(psortb_docker, "localize", mock_psortb_localize)
    return state


@pytest.fixture
def smoke_fixture_set(
    mock_iedb_post: MockIEDBPost,
    mock_ebi_psortb: dict[str, Any],
    fixture_proteome: list[dict[str, Any]],
    fixture_epitopes: dict[str, list[iedb.EpitopePrediction]],
) -> dict[str, Any]:
    """One fixture bundle for a fast harness smoke check and future test setup."""
    return {
        "iedb": mock_iedb_post,
        "ebi_psortb": mock_ebi_psortb,
        "proteome": fixture_proteome,
        "epitopes": fixture_epitopes,
    }
