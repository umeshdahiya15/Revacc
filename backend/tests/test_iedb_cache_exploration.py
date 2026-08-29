"""Bug-condition exploration for missing IEDB response caching.

This test intentionally fails against the unfixed implementation: identical
prediction requests currently issue a fresh HTTP fetch on every call.
"""
from __future__ import annotations

from collections.abc import Callable

import pytest

from app.tools import api_cache, iedb


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("predictor", "lengths", "method"),
    [
        (iedb.predict_mhci, ("9",), "netmhcpan_el"),
        (iedb.predict_mhcii, ("15",), "netmhciipan_el"),
    ],
)
async def test_identical_iedb_predictions_reuse_cached_response(
    predictor: Callable[..., object],
    lengths: tuple[str, ...],
    method: str,
    mock_iedb_post,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """Property 2: identical IEDB inputs must not re-fetch the HTTP response."""
    # Keep the future cache-backed implementation deterministic and isolated
    # from any cache files left by other tests or local development runs.
    monkeypatch.setattr(api_cache, "_CACHE_DIR", str(tmp_path))
    queries = [("fixture-protein", "M" + "A" * 59)]
    alleles = ["HLA-A*02:01"] if predictor is iedb.predict_mhci else ["HLA-DRB1*01:01"]

    first = await predictor(queries, alleles=alleles, lengths=lengths, method=method)
    second = await predictor(queries, alleles=alleles, lengths=lengths, method=method)

    assert second == first
    assert len(mock_iedb_post.calls) <= 1, (
        "identical IEDB requests should reuse the successful cached response; "
        f"underlying fetch count was {len(mock_iedb_post.calls)}"
    )
