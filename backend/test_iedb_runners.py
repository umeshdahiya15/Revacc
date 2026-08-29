"""Mocked integration test for the IEDB epitope runners (steps 5-1 and 6-1).

Follows the project's Python-mocks approach (no pytest): the engine is driven
with `IEDB` patched so no real network call is made, verifying that:

  1. the essential candidates from step 2-1 survive the session prune;
  2. the 5-1 / 6-1 runners translate IEDB TSV rows into `Epitope` records
     on the job and count them in the funnel;
  3. GET /api/jobs/{id}/epitopes returns the stored predictions.
"""
from __future__ import annotations

import asyncio
import unittest
from unittest import mock

import app.simulator as simulator
from app.models import Epitope, JobCreate
from app.repo import repo
from app.routes import job_epitopes
from app.tools import runner as runner_mod

MHCI_TSV = (
    "allele\tseq_num\tstart\tend\tlength\tpeptide\tcore\ticore\tscore\tpercentile_rank\n"
    "HLA-A*02:01\t1\t1\t9\t9\tSVPNKLSYL\tSVPNKLSYL\tSVPNKLSYL\t0.82\t0.06\n"
    "HLA-A*02:01\t1\t2\t10\t9\tVPNKLSYLA\tVPNKLSYLA\tVPNKLSYLA\t0.05\t12.3\n"
    "HLA-A*02:01\t2\t1\t9\t9\tYTFATVAPV\tYTFATVAPV\tYTFATVAPV\t0.78\t0.12\n"
)
MHCII_TSV = (
    "allele\tseq_num\tstart\tend\tlength\tcore_peptide\tpeptide\tscore\trank\n"
    "HLA-DRB1*01:01\t1\t1\t15\t15\tAHKVPRRLL\tGHAHKVPRRLLKAAR\t0.9\t0.4\n"
    "HLA-DRB1*01:01\t1\t2\t16\t15\tHKVPRRLLK\tHAHKVPRRLLKAARR\t0.3\t24.0\n"
)


def _epitope_hits(mhci: bool):
    if mhci:
        return runner_mod.iedb._parse_tsv(MHCI_TSV, has_rank=False)
    return runner_mod.iedb._parse_tsv(MHCII_TSV, has_rank=True)


class IEDBRunnerTest(unittest.TestCase):
    def setUp(self) -> None:
        # Fresh in-memory repo, no seed, so job ids are stable.
        repo._jobs.clear()
        repo._events.clear()
        repo._lock = __import__("threading").RLock()
        runner_mod._RUN_SESSION.clear()
        simulator.engine._task = None
        simulator.engine._stopping = True

    def _make_job(self) -> object:
        create = JobCreate(
            name="IEDB integration test",
            pathogenName="Streptococcus agalactiae",
            taxonId=208435,
            hlaMhc1=["HLA-A*02:01"],
            hlaMhc2=["HLA-DRB1*01:01"],
            realTools=True,
        )
        return repo.create(create)

    def _seed_candidates(self, job) -> None:
        session = runner_mod.get_session(job.id)
        session["essential"] = {
            "indices": [0, 1],
            "candidates": [
                {"index": 0, "uniprotId": "Q8E1E1", "name": "Sip", "sequence": "SVPNKLSYLAVLKK"},
                {"index": 1, "uniprotId": "Q8E0S8", "name": "ScpB", "sequence": "YTFATVAPVGGGGG"},
            ],
        }

    async def _run_step(self, job, step_id: str) -> dict:
        step = next(s for ph in job.phases for s in ph.steps if s.id == step_id)
        return await runner_mod.run_runner(job, step, timeout=runner_mod.runner_timeout(step_id))

    def _loop(self) -> asyncio.AbstractEventLoop:
        try:
            return asyncio.get_running_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            return loop

    def test_parse_tsv_shapes(self) -> None:
        hits = _epitope_hits(mhci=True)
        self.assertEqual(len(hits), 3)
        self.assertEqual(hits[0].peptide, "SVPNKLSYL")
        self.assertAlmostEqual(hits[0].percentile_rank, 0.06)
        hits2 = _epitope_hits(mhci=False)
        self.assertEqual(hits2[0].core, "AHKVPRRLL")
        self.assertAlmostEqual(hits2[0].percentile_rank, 0.4)

    def test_prune_session_keeps_essential(self) -> None:
        job = self._make_job()
        session = runner_mod.get_session(job.id)
        session["proteins"] = [object()] * 10
        session["clusters"] = {"indices": [0], "representatives": ["AAAA"]}
        session["essential"] = {"indices": [0], "candidates": []}
        session["epitopes"] = []
        runner_mod.prune_session(job.id, keep=("essential", "epitopes"))
        kept = runner_mod.get_session_peek(job.id)
        self.assertNotIn("proteins", kept)
        self.assertNotIn("clusters", kept)
        self.assertIn("essential", kept)
        self.assertIn("epitopes", kept)

    def test_runner_5_1_persists_epitopes_and_funnel(self) -> None:
        job = self._make_job()
        self._seed_candidates(job)

        with mock.patch.object(runner_mod.iedb, "predict_mhci", side_effect=lambda q, alleles, **kwargs: _epitope_hits(mhci=True)) as pred:
            result = self._loop().run_until_complete(self._run_step(job, "5-1"))

        self.assertEqual(pred.call_count, 1)
        self.assertEqual(result["selected"], 2)  # top per protein (SVPNKLSYL, YTFATVAPV)
        session = runner_mod.get_session_peek(job.id)
        self.assertEqual(len(session["epitopes"]), 2)
        self.assertTrue(all(epitope["source"] == "real" for epitope in session["epitopes"]))

        # Simulate the engine post-step bookkeeping (funnel + job.epitopes).
        async def _sim():
            step = next(s for ph in job.phases for s in ph.steps if s.id == "5-1")
            step.status = "success"
            step.result = result
            await simulator.engine._update_funnel(job, step)
            job.epitopes = [Epitope(**e) for e in session["epitopes"]]

        self._loop().run_until_complete(_sim())
        self.assertEqual(len(job.epitopes), 2)
        self.assertEqual(job.epitopes[0].type, "CTL")
        self.assertEqual(job.epitopes[0].sourceProtein, "Q8E1E1")
        mhc_i = next(x for x in job.funnel if x["key"] == "mhc-i")
        self.assertEqual(mhc_i["count"], 2)
        self.assertEqual(mhc_i.get("final"), True)  # last stage until 6-1 adds mhc-ii

    def test_runner_6_1_htl_and_funnel_final(self) -> None:
        job = self._make_job()
        self._seed_candidates(job)

        with mock.patch.object(runner_mod.iedb, "predict_mhcii", side_effect=lambda q, alleles, **kwargs: _epitope_hits(mhci=False)) as pred:
            result = self._loop().run_until_complete(self._run_step(job, "6-1"))

        self.assertEqual(pred.call_count, 1)
        self.assertEqual(result["selected"], 1)  # only the strong binder
        session = runner_mod.get_session_peek(job.id)
        self.assertEqual(len(session["epitopes"]), 1)
        self.assertTrue(all(epitope["source"] == "real" for epitope in session["epitopes"]))
        self.assertEqual(session["epitopes"][0]["type"], "HTL")

        async def _sim():
            step = next(s for ph in job.phases for s in ph.steps if s.id == "6-1")
            step.status = "success"
            step.result = result
            await simulator.engine._update_funnel(job, step)
            job.epitopes = [Epitope(**e) for e in session["epitopes"]]

        self._loop().run_until_complete(_sim())
        mhc_ii = next(x for x in job.funnel if x["key"] == "mhc-ii")
        self.assertEqual(mhc_ii["count"], 1)
        self.assertTrue(mhc_ii["final"])

    def test_runner_iedb_outage_raises_honest_unavailable_error(self) -> None:
        """An IEDB outage must not produce synthetic scientific epitope rows."""
        from app.tools.graceful_pause import ToolUnavailableError

        job = self._make_job()
        self._seed_candidates(job)

        with mock.patch.object(runner_mod.iedb, "predict_mhci", side_effect=ConnectionError("IEDB unavailable")):
            with self.assertRaises(ToolUnavailableError) as caught:
                self._loop().run_until_complete(self._run_step(job, "5-1"))

        self.assertIn("IEDB", caught.exception.tool_name)
        self.assertIn("no synthetic epitopes", str(caught.exception).lower())
        session = runner_mod.get_session_peek(job.id)
        self.assertEqual(session.get("epitopes", []), [])

    def test_runner_requires_candidates(self) -> None:
        job = self._make_job()
        with self.assertRaises(RuntimeError):
            self._loop().run_until_complete(self._run_step(job, "5-1"))

    def test_epitopes_endpoint(self) -> None:
        job = self._make_job()
        self._seed_candidates(job)
        with mock.patch.object(runner_mod.iedb, "predict_mhci", side_effect=lambda q, alleles, **kwargs: _epitope_hits(mhci=True)):
            self._loop().run_until_complete(self._run_step(job, "5-1"))
        session = runner_mod.get_session_peek(job.id)
        job.epitopes = [Epitope(**e) for e in session["epitopes"]]
        repo.upsert(job)

        data = [e.model_dump(mode="json") for e in job_epitopes(job.id)]
        self.assertEqual(len(data), 2)
        self.assertEqual(data[0]["type"], "CTL")
        htl = [e.model_dump(mode="json") for e in job_epitopes(job.id, type="HTL")]
        self.assertEqual(len(htl), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
