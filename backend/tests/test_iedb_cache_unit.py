"""Focused cache-key and row-serialization coverage for the IEDB client."""
from __future__ import annotations

import pytest

from app.tools import api_cache, iedb


@pytest.mark.asyncio
async def test_iedb_cache_key_separates_request_inputs_and_round_trips_rows(
    mock_iedb_post,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setattr(api_cache, "_CACHE_DIR", str(tmp_path))
    query = [("protein", "M" + "A" * 59)]
    common = {"alleles": ["HLA-A*02:01"], "lengths": ("9",), "method": "netmhcpan_el"}

    first = await iedb.predict_mhci(query, **common)
    second = await iedb.predict_mhci(query, **common)
    changed_sequence = await iedb.predict_mhci(
        [("protein", "M" + "C" * 59)], **common
    )
    changed_method = await iedb.predict_mhci(
        query, **{**common, "method": "consensus"}
    )
    changed_lengths = await iedb.predict_mhci(
        query, **{**common, "lengths": ("10",)}
    )
    changed_allele = await iedb.predict_mhci(
        query, **{**common, "alleles": ["HLA-A*01:01"]}
    )

    assert second == first
    assert changed_sequence == first
    assert changed_method == first
    assert changed_lengths == first
    assert changed_allele == first
    assert len(mock_iedb_post.calls) == 5
    assert all(isinstance(row, iedb.EpitopePrediction) for row in second)
