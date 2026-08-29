"""Happy-path preservation tests for the live IEDB prediction path.

These tests deliberately exercise the unfixed implementation with a successful,
offline IEDB response.  They lock down batching, cooldown, rank filtering,
global sequence-number remapping, and runner source attribution before the
cache/fallback fix is applied.
"""
from __future__ import annotations

import asyncio
from typing import Any, Callable

import pytest

from app.models import JobCreate
from app.repo import repo
from app.tools import api_cache, iedb
from app.tools import runner as runner_mod


CANDIDATE_IDS = [f"FIX-IEDB-{i:03d}" for i in range(5)]


class SuccessfulIEDBPost:
    """Deterministic successful IEDB response with valid rows per batch."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def __call__(self, _client: Any, url: str, data: dict[str, str]) -> Any:
        self.calls.append({"url": url, "data": data.copy()})
        query_count = sum(line.startswith(">") for line in data["sequence_text"].splitlines())
        mhcii = "mhcii" in url
        length = 15 if mhcii else 9
        peptide = "ACDEFGHIKLMNPQR"[:length]
        rows = []
        for seq_num in range(1, query_count + 1):
            rows.append(
                f"{data['allele'].split(',')[0]}\t{seq_num}\t1\t{length}\t{length}\t"
                f"{peptide}\t{peptide if mhcii else ''}\t0.9\t1.0"
            )
            rows.append(
                f"{data['allele'].split(',')[0]}\t{seq_num}\t2\t{length + 1}\t{length}\t"
                f"{peptide}\t{peptide if mhcii else ''}\t0.1\t2.5"
            )
        header = (
            "allele\tseq_num\tstart\tend\tlength\tcore_peptide\tpeptide\tscore\trank"
            if mhcii
            else "allele\tseq_num\tstart\tend\tlength\tpeptide\tcore\tscore\tpercentile_rank"
        )
        return type("Response", (), {"text": header + "\n" + "\n".join(rows), "status_code": 200})()


@pytest.fixture
def successful_iedb_post(monkeypatch: pytest.MonkeyPatch) -> SuccessfulIEDBPost:
    # Keep this preservation fixture independent from cache-property tests and
    # assert the live HTTP batching path on every invocation.
    monkeypatch.setattr(api_cache, "_read_cache", lambda *args, **kwargs: None)
    monkeypatch.setattr(api_cache, "_write_cache", lambda *args, **kwargs: None)
    post = SuccessfulIEDBPost()
    monkeypatch.setattr(iedb, "_post", post)
    return post


@pytest.fixture
def no_iedb_wait(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    delays: list[float] = []

    async def record_delay(seconds: float) -> None:
        delays.append(seconds)

    monkeypatch.setattr(iedb.asyncio, "sleep", record_delay)
    return delays


def _reset_pipeline_state() -> None:
    repo._jobs.clear()
    repo._events.clear()
    runner_mod._RUN_SESSION.clear()


def _queries() -> list[tuple[str, str]]:
    return [(f">{i}|{candidate}", "M" + "A" * 59) for i, candidate in enumerate(CANDIDATE_IDS)]


@pytest.mark.parametrize(
    ("predictor", "alleles", "lengths", "method", "mhci"),
    [
        (iedb.predict_mhci, ["HLA-A*02:01"], ("9",), "netmhcpan_el", True),
        (iedb.predict_mhcii, ["HLA-DRB1*01:01"], ("15",), "consensus", False),
    ],
)
def test_successful_iedb_predictions_preserve_batches_and_global_seq_nums(
    predictor: Any,
    alleles: list[str],
    lengths: tuple[str, ...],
    method: str,
    mhci: bool,
    successful_iedb_post: SuccessfulIEDBPost,
    no_iedb_wait: list[float],
) -> None:
    """Property 8: live predictors retain batching, cooldown, ranks, and remapping.

    **Validates: Requirements 3.1, 3.2**
    """
    predictions = asyncio.run(predictor(_queries(), alleles=alleles, lengths=lengths, method=method))

    assert len(successful_iedb_post.calls) == 2
    assert [
        sum(line.startswith(">") for line in call["data"]["sequence_text"].splitlines())
        for call in successful_iedb_post.calls
    ] == [iedb.BATCH_SIZE, 1]
    assert no_iedb_wait == [iedb.BATCH_COOLDOWN_SEC]
    assert all(call["data"]["method"] == method for call in successful_iedb_post.calls)

    # Each query has one strong row and one row just outside the <=2 cutoff.
    strong = iedb.strong_binders(predictions, mhci=mhci, threshold=2.0)
    assert len(strong) == len(CANDIDATE_IDS)
    assert [prediction.seq_num for prediction in strong] == list(range(1, 6))
    assert all(prediction.percentile_rank == 1.0 for prediction in strong)
    assert len(iedb.strong_binders(predictions, mhci=mhci, threshold=1.0)) == 5


@pytest.mark.parametrize(("step_id", "kind", "alleles"), [("5-1", "CTL", ["HLA-A*02:01"]), ("6-1", "HTL", ["HLA-DRB1*01:01"])])
@pytest.mark.parametrize("order", [list(range(5)), [4, 3, 2, 1, 0], [2, 4, 0, 3, 1]])
def test_successful_iedb_runners_preserve_live_attribution(
    step_id: str,
    kind: str,
    alleles: list[str],
    order: list[int],
    successful_iedb_post: SuccessfulIEDBPost,
    no_iedb_wait: list[float],
) -> None:
    """Property 8: successful CTL/HTL rows remain live and map to source proteins.

    **Validates: Requirements 3.1, 3.2**
    """
    _reset_pipeline_state()
    candidates = [
        {
            "index": index,
            "uniprotId": CANDIDATE_IDS[index],
            "name": f"Fixture protein {index}",
            "sequence": "M" + "A" * 59,
        }
        for index in order
    ]
    job = repo.create(
        JobCreate(
            name=f"IEDB {kind} preservation",
            hlaMhc1=alleles if kind == "CTL" else [],
            hlaMhc2=alleles if kind == "HTL" else [],
            mhciPercentile=2.0,
            mhciiPercentile=2.0,
            realTools=True,
        )
    )
    runner_mod.get_session(job.id)["essential"] = {"indices": list(order), "candidates": candidates}
    step = next(step for phase in job.phases for step in phase.steps if step.id == step_id)

    result = asyncio.run(runner_mod.run_runner(job, step, timeout=120.0))
    epitopes = runner_mod.get_session_peek(job.id)["epitopes"]

    assert result["predicted"] == 10
    assert result["strongBinders"] == 5
    assert result["selected"] == 5
    assert len(epitopes) == 5
    assert all(epitope["type"] == kind for epitope in epitopes)
    assert all(epitope["percentileRank"] == 1.0 for epitope in epitopes)
    assert {
        (epitope["sourceProtein"], epitope["sourceProteinId"], epitope["sourceProteinName"])
        for epitope in epitopes
    } == {
        (candidate["uniprotId"], str(candidate["index"]), candidate["name"])
        for candidate in candidates
    }
    serialized = str(epitopes).lower()
    assert "fallback" not in serialized
    assert "cache" not in serialized
    assert all("iedb" in (epitope["predictionMethod"] or "").lower() for epitope in epitopes)
    assert len(successful_iedb_post.calls) == 2
    assert no_iedb_wait == [iedb.BATCH_COOLDOWN_SEC]
