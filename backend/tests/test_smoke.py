"""Smoke checks proving the pipeline-fixes pytest fixture harness is usable."""
from __future__ import annotations

import pytest

from app.tools import iedb


@pytest.mark.asyncio
async def test_shared_mock_harness_smoke(smoke_fixture_set: dict) -> None:
    bundle = smoke_fixture_set
    response = await iedb._post(None, "https://mock/tools_api/mhci/", {})

    assert response.status_code == 200
    assert bundle["iedb"].calls[0]["url"].endswith("mhci/")
    assert {p["expected_localization"] for p in bundle["proteome"]} >= {
        "OuterMembrane",
        "Lipoprotein",
        "CellWall",
    }
    assert bundle["epitopes"]["mhci"][0].peptide == "SVPNKLSYL"
    assert bundle["epitopes"]["mhcii"][0].percentile_rank == 0.4


def test_fixture_bundle_has_expected_categories(smoke_fixture_set: dict) -> None:
    assert len(smoke_fixture_set["proteome"]) == 5
    assert len(smoke_fixture_set["epitopes"]["mhci"]) == 2
