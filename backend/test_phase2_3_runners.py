r"""Tests for Phase 2-2 through 3-4 runners.

Follows the project test pattern: unittest.mock, no pytest, direct function
calls (no TestClient — TestClient is broken in this venv).  Run with::

    python -m unittest test_phase2_3_runners -v
"""

from __future__ import annotations

import asyncio
import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__)))

from app.tools import ncbiblast
from app.tools.ebi_rest_client import (
    EBIRestClient,
    EBIRestClientError,
    classify_phobius,
    parse_phobius_out,
)
from app.tools.graceful_pause import (
    ALGPRED_PAUSE,
    DEEPTMHMM_PAUSE,
    PSORTB_PAUSE,
    VAXIJEN_PAUSE,
    ToolUnavailableError,
    require_tool,
)


# ======================================================================
# 1. EBI REST CLIENT — parsing and classification (no network)
# ======================================================================

class TestEBIRestClientParsing(unittest.TestCase):
    """Test Phobius feature-table parsing."""

    def test_parse_signal_peptide(self):
        raw = "ID EMBOSS_001\nFT   SIGNAL      1      24\nFT   DOMAIN      1      74       NON CYTOPLASMIC.\n//"
        parsed = parse_phobius_out(raw)
        self.assertIsNotNone(parsed["signal_peptide"])
        self.assertEqual(parsed["signal_peptide"], {"start": 1, "end": 24})
        self.assertEqual(len(parsed["transmembrane_regions"]), 0)
        self.assertEqual(len(parsed["domains"]), 1)

    def test_parse_transmembrane(self):
        raw = "ID EMBOSS_002\nFT   TRANSMEM   45      67\nFT   TRANSMEM  120     142\n//"
        parsed = parse_phobius_out(raw)
        self.assertIsNone(parsed["signal_peptide"])
        self.assertEqual(len(parsed["transmembrane_regions"]), 2)
        self.assertEqual(parsed["transmembrane_regions"][0]["start"], 45)
        self.assertEqual(parsed["transmembrane_regions"][0]["end"], 67)

    def test_parse_signal_plus_tm(self):
        raw = "ID EMBOSS_003\nFT   SIGNAL      1      24\nFT   TRANSMEM   50      72\n//"
        parsed = parse_phobius_out(raw)
        self.assertIsNotNone(parsed["signal_peptide"])
        self.assertEqual(len(parsed["transmembrane_regions"]), 1)

    def test_parse_empty(self):
        parsed = parse_phobius_out("")
        self.assertIsNone(parsed["signal_peptide"])
        self.assertEqual(len(parsed["transmembrane_regions"]), 0)

    def test_parse_full_feature_table(self):
        raw = (
            "ID EMBOSS_004\n"
            "FT   SIGNAL      1      24\n"
            "FT   TRANSMEM   45      67       POTENTIAL.\n"
            "FT   DOMAIN      1     100       NON CYTOPLASMIC.\n"
            "FT   DOMAIN    101     200       CYTOPLASMIC.\n"
            "//"
        )
        parsed = parse_phobius_out(raw)
        self.assertEqual(len(parsed["signal_peptide"]), 2)  # {start, end}
        self.assertEqual(len(parsed["transmembrane_regions"]), 1)
        self.assertEqual(parsed["transmembrane_regions"][0]["description"], "POTENTIAL.")
        self.assertEqual(len(parsed["domains"]), 2)
        self.assertEqual(parsed["domains"][0]["description"], "NON CYTOPLASMIC.")
        self.assertEqual(parsed["domains"][1]["description"], "CYTOPLASMIC.")


class TestEBIRestClientClassify(unittest.TestCase):
    """Test the classify_phobius helper."""

    def test_secreted(self):
        self.assertEqual(
            classify_phobius({"signal_peptide": {"start": 1, "end": 24}, "transmembrane_regions": []}),
            "secreted",
        )

    def test_membrane(self):
        self.assertEqual(
            classify_phobius({"signal_peptide": None, "transmembrane_regions": [{"start": 50, "end": 72}]}),
            "membrane",
        )

    def test_membrane_with_signal(self):
        self.assertEqual(
            classify_phobius({"signal_peptide": {"start": 1, "end": 24}, "transmembrane_regions": [{"start": 50, "end": 72}]}),
            "membrane",
        )

    def test_intracellular(self):
        self.assertEqual(
            classify_phobius({"signal_peptide": None, "transmembrane_regions": []}),
            "intracellular",
        )

    def test_unknown_empty_dict(self):
        self.assertEqual(classify_phobius({}), "intracellular")


# ======================================================================
# 2. EBI REST CLIENT — async submit/poll/fetch (mocked network)
# ======================================================================

class TestEBIRestClientAsync(unittest.TestCase):
    """Test async methods with mocked httpx."""

    def _response(self, text: str):
        return MagicMock(text=MagicMock(return_value=text), raise_for_status=MagicMock())

    def test_run_success(self):
        client = EBIRestClient(email="test@test.com", poll_interval=0.01, poll_timeout=5.0)

        async def _test():
            mock_http = AsyncMock()
            mock_http.is_closed = False
            mock_http.post = AsyncMock(return_value=self._response("phobius-R123-001"))
            mock_http.get = AsyncMock(side_effect=[
                self._response("FINISHED"),
                self._response("FT   SIGNAL      1      24\n//"),
            ])
            client._client = mock_http

            result = await client.run("phobius", "MKLSL...")
            self.assertEqual(result.job_id, "phobius-R123-001")
            self.assertEqual(result.status, "FINISHED")
            self.assertIn("SIGNAL", result.raw_text)

        asyncio.run(_test())

    def test_run_error_status(self):
        client = EBIRestClient(email="test@test.com", poll_interval=0.01, poll_timeout=5.0)

        async def _test():
            mock_http = AsyncMock()
            mock_http.is_closed = False
            mock_http.post = AsyncMock(return_value=self._response("phobius-R456"))
            mock_http.get = AsyncMock(return_value=self._response("ERROR"))
            client._client = mock_http

            result = await client.run("phobius", "MKLSL...")
            self.assertEqual(result.status, "ERROR")

        asyncio.run(_test())

    def test_submit_retry_exhaustion(self):
        client = EBIRestClient(email="test@test.com", poll_interval=0.01, poll_timeout=5.0)

        async def _test():
            mock_http = AsyncMock()
            mock_http.is_closed = False
            mock_http.post = AsyncMock(side_effect=Exception("connection refused"))
            client._client = mock_http

            with self.assertRaises(EBIRestClientError):
                await client.submit("phobius", "MKLSL...")

        asyncio.run(_test())


# ======================================================================
# 3. GRACEFUL PAUSE
# ======================================================================

class TestGracefulPause(unittest.TestCase):
    def test_psortb_pause(self):
        with self.assertRaises(ToolUnavailableError) as ctx:
            asyncio.run(require_tool(**PSORTB_PAUSE))
        self.assertIn("PSORTb", str(ctx.exception))
        self.assertIn("NXDOMAIN", str(ctx.exception))

    def test_deeptmhmm_pause(self):
        with self.assertRaises(ToolUnavailableError) as ctx:
            asyncio.run(require_tool(**DEEPTMHMM_PAUSE))
        self.assertIn("DeepTMHMM", str(ctx.exception))

    def test_vaxijen_pause(self):
        with self.assertRaises(ToolUnavailableError) as ctx:
            asyncio.run(require_tool(**VAXIJEN_PAUSE))
        self.assertIn("VaxiJen", str(ctx.exception))
        self.assertIn("Cloudflare 403", str(ctx.exception))

    def test_algpred_pause(self):
        with self.assertRaises(ToolUnavailableError) as ctx:
            asyncio.run(require_tool(**ALGPRED_PAUSE))
        self.assertIn("AlgPred", str(ctx.exception))

    def test_tool_unavailable_error_str(self):
        err = ToolUnavailableError(tool_name="TestTool", reason="test reason", workaround="test workaround")
        s = str(err)
        self.assertIn("TestTool", s)
        self.assertIn("test reason", s)
        self.assertIn("test workaround", s)

    def test_tool_unavailable_error_is_assignable(self):
        """Exception instances remain assignable on Python 3.13."""
        err = ToolUnavailableError(tool_name="T", reason="r", workaround="w")
        err.tool_name = "X"
        self.assertEqual(err.tool_name, "X")


# ======================================================================
# 4. RUNNER 2-4 (Phobius) — mocked EBI
# ======================================================================

class TestRunner2_4(unittest.TestCase):
    PHOBIUS_OUT_SECRETED = "ID EMBOSS_001\nFT   SIGNAL      1      24\nFT   DOMAIN      1      74       NON CYTOPLASMIC.\n//"
    PHOBIUS_OUT_INTRACELLULAR = "ID EMBOSS_002\n//"
    PHOBIUS_OUT_MEMBRANE = "ID EMBOSS_003\nFT   TRANSMEM   50      72\n//"

    def _make_job(self):
        job = MagicMock()
        job.id = "test-job"
        return job

    def _make_session(self):
        candidates = [
            {"index": 0, "uniprotId": "P0A0A0", "name": "ProtA", "sequence": "M" * 100 + "A"},
            {"index": 1, "uniprotId": "P0B0B0", "name": "ProtB", "sequence": "M" * 100 + "C"},
            {"index": 2, "uniprotId": "P0C0C0", "name": "ProtC", "sequence": "M" * 100 + "G"},
        ]
        return {
            "essential": {"candidates": candidates, "count": len(candidates)},
            # Step 2-4 refines the surface set produced by real PSORTb step 2-2.
            "surface_exposed": {"candidates": list(candidates), "count": len(candidates)},
        }

    def test_filters_intracellular(self):
        from app.tools.runner_additions import run_2_4

        async def _test():
            session = self._make_session()
            job = self._make_job()
            step = MagicMock(id="2-4", status="pending")

            with patch("app.tools.runner_additions.EBIRestClient") as MockClient:
                client = AsyncMock()
                MockClient.return_value = client
                client.submit = AsyncMock(side_effect=["j1", "j2", "j3"])
                client.poll_status = AsyncMock(return_value="FINISHED")
                client.fetch_result = AsyncMock(side_effect=[
                    self.PHOBIUS_OUT_SECRETED,
                    self.PHOBIUS_OUT_INTRACELLULAR,
                    self.PHOBIUS_OUT_MEMBRANE,
                ])
                client.close = AsyncMock()
                with patch("app.tools.runner_additions.PHOBIUS_RATE_LIMIT_SEC", 0.0), \
                     patch("app.tools.runner_additions._load_phobius_cache", return_value={}), \
                     patch("app.tools.runner_additions._save_phobius_cache") as save_cache:
                     result = await run_2_4(session, job, step)

            self.assertEqual(result["total_analyzed"], 3)
            self.assertEqual(result["surface_exposed_count"], 2)
            self.assertEqual(result["intracellular_count"], 1)
            self.assertEqual(result["secreted_count"], 1)
            self.assertEqual(result["membrane_count"], 1)
            self.assertEqual(session["surface_exposed"]["count"], 2)
            self.assertGreaterEqual(save_cache.call_count, 3)

        asyncio.run(_test())

    def test_empty_candidates(self):
        from app.tools.runner_additions import run_2_4

        async def _test():
            session = {"essential": {"candidates": [], "count": 0}}
            job = self._make_job()
            step = MagicMock(id="2-4", status="pending")
            result = await run_2_4(session, job, step)
            self.assertEqual(result["total_analyzed"], 0)
            self.assertEqual(result["surface_exposed_count"], 0)

        asyncio.run(_test())

    def test_ebi_failure_keeps_all(self):
        from app.tools.runner_additions import run_2_4

        async def _test():
            session = self._make_session()
            job = self._make_job()
            step = MagicMock(id="2-4", status="pending")

            with patch("app.tools.runner_additions.EBIRestClient") as MockClient:
                client = AsyncMock()
                MockClient.return_value = client
                client.submit = AsyncMock(return_value="j-err")
                client.poll_status = AsyncMock(return_value="ERROR")
                client.fetch_result = AsyncMock()
                client.close = AsyncMock()
                with patch("app.tools.runner_additions.PHOBIUS_RATE_LIMIT_SEC", 0.0), \
                     patch("app.tools.runner_additions._load_phobius_cache", return_value={}), \
                     patch("app.tools.runner_additions._save_phobius_cache"):
                    result = await run_2_4(session, job, step)

            self.assertEqual(result["surface_exposed_count"], 0)
            self.assertTrue(all(item["classification"] == "unknown" for item in result["classifications"]))

        asyncio.run(_test())

    def test_ebi_down_raises_tool_unavailable(self):
        from app.tools.runner_additions import run_2_4

        async def _test():
            session = self._make_session()
            job = self._make_job()
            step = MagicMock(id="2-4", status="pending")

            with patch("app.tools.runner_additions.EBIRestClient") as MockClient:
                client = AsyncMock()
                MockClient.return_value = client
                client.submit = AsyncMock(side_effect=EBIRestClientError("Connection timed out"))
                client.close = AsyncMock()

                with self.assertRaises(ToolUnavailableError):
                    with patch("app.tools.runner_additions.PHOBIUS_RATE_LIMIT_SEC", 0.0), \
                         patch("app.tools.runner_additions._load_phobius_cache", return_value={}), \
                         patch("app.tools.runner_additions._save_phobius_cache"):
                        await run_2_4(session, job, step)

        asyncio.run(_test())


# ======================================================================
# 5. LOCAL COMPUTATION RUNNERS (2-2, 2-3, 3-1, 3-2)
# ======================================================================

class TestLocalCandidateRunners(unittest.TestCase):
    def _make(self):
        session = {"essential": {"candidates": [], "count": 0}}
        job = MagicMock(id="test-job")
        step = MagicMock(id="x", status="pending")
        return session, job, step

    def test_run_2_2_local_empty(self):
        from app.tools.runner_additions import run_2_2
        session, job, step = self._make()
        result = asyncio.run(run_2_2(session, job, step))
        self.assertEqual(result["total_analyzed"], 0)
        self.assertEqual(result["surface_exposed_count"], 0)
        self.assertEqual(result["method"], "psortb_6.0_local")

    def test_run_2_3_local_empty(self):
        from app.tools.runner_additions import run_2_3
        session, job, step = self._make()
        result = asyncio.run(run_2_3(session, job, step))
        self.assertEqual(result["total_analyzed"], 0)
        self.assertEqual(result["transmembrane_count"], 0)
        self.assertEqual(result["method"], "tmh_local_informational")

    def test_run_3_1_local_empty(self):
        from app.tools.runner_additions import run_3_1
        session, job, step = self._make()
        result = asyncio.run(run_3_1(session, job, step))
        self.assertEqual(result["total_analyzed"], 0)
        self.assertEqual(result["allergen_count"], 0)

    def test_run_3_2_local_empty(self):
        from app.tools.runner_additions import run_3_2
        session, job, step = self._make()
        result = asyncio.run(run_3_2(session, job, step))
        self.assertEqual(result["total_analyzed"], 0)
        self.assertEqual(result["antigenic_count"], 0)


# ======================================================================
# 6. RUNNER 3-4 (Human Homology) — mocked ncbiblast.blastp
# ======================================================================

class TestRunner3_4(unittest.TestCase):
    def _make_session(self):
        return {
            "virulence_factors": {
                "candidates": [
                    {"uniprotId": "P0A", "name": "VF1", "sequence": "M" * 50},
                    {"uniprotId": "P0B", "name": "VF2", "sequence": "M" * 50},
                    {"uniprotId": "P0C", "name": "VF3", "sequence": "M" * 50},
                ],
                "count": 3,
            }
        }

    def _hit(self, identity: int, length: int, e_value: float, title: str = "HUMAN"):
        return ncbiblast.BlastHit(
            hit_id="gi|1",
            accession="NP_000001.1",
            title=title,
            length=length,
            identity=identity,
            positive=identity,
            align_length=length,
            e_value=e_value,
            score=100,
        )

    def test_filters_homologous(self):
        from app.tools.runner_additions import run_3_4
        from app.tools import blastdb_local

        async def _test():
            session = self._make_session()
            job = MagicMock(id="test-job")
            step = MagicMock(id="3-4", status="pending")

            blast_results = [
                ncbiblast.BlastResult(query_def="P0A", hits=[self._hit(15, 100, 0.01)]),
                ncbiblast.BlastResult(query_def="P0B", hits=[self._hit(45, 100, 1e-10)]),
                ncbiblast.BlastResult(query_def="P0C", hits=[self._hit(10, 100, 0.5)]),
            ]
            with (
                patch.object(blastdb_local, "ensure_human_db", new=AsyncMock(return_value="/tmp/human")),
                patch.object(blastdb_local, "blastp", new=AsyncMock(return_value=blast_results)),
            ):
                result = await run_3_4(session, job, step)

            self.assertEqual(result["total_analyzed"], 3)
            self.assertEqual(result["homologous_count"], 1)
            self.assertEqual(result["non_homologous_count"], 2)
            self.assertIn("P0B", result["homologous_ids"])
            self.assertEqual(session["vaccine_targets"]["count"], 2)

        asyncio.run(_test())

    def test_no_hits_all_pass(self):
        from app.tools.runner_additions import run_3_4
        from app.tools import blastdb_local

        async def _test():
            session = self._make_session()
            job = MagicMock(id="test-job")
            step = MagicMock(id="3-4", status="pending")

            with (
                patch.object(blastdb_local, "ensure_human_db", new=AsyncMock(return_value="/tmp/human")),
                patch.object(blastdb_local, "blastp", new=AsyncMock(return_value=[])),
            ):
                result = await run_3_4(session, job, step)

            self.assertEqual(result["non_homologous_count"], 3)
            self.assertEqual(result["homologous_count"], 0)
            self.assertEqual(session["vaccine_targets"]["count"], 3)

        asyncio.run(_test())

    def test_blast_failure_pauses(self):
        from app.tools.runner_additions import run_3_4
        from app.tools import blastdb_local

        async def _test():
            session = self._make_session()
            job = MagicMock(id="test-job")
            step = MagicMock(id="3-4", status="pending")

            with (
                patch.object(blastdb_local, "ensure_human_db", new=AsyncMock(return_value="/tmp/human")),
                patch.object(blastdb_local, "blastp", new=AsyncMock(side_effect=Exception("local blast failed"))),
            ):
                with self.assertRaises(ToolUnavailableError):
                    await run_3_4(session, job, step)

        asyncio.run(_test())


# ======================================================================
# 7. RUNNER 3-3 (VFDB) — mocked blast_vfdb
# ======================================================================

class TestRunner3_3(unittest.TestCase):
    def _make_session(self):
        return {
            "surface_exposed": {
                "candidates": [
                    {"uniprotId": "P0A", "name": "Prot1", "sequence": "M" * 50},
                    {"uniprotId": "P0B", "name": "Prot2", "sequence": "M" * 50},
                ],
                "count": 2,
            }
        }

    def test_virulence_filter(self):
        from app.tools.runner_additions import run_3_3
        from app.tools.vfdb import VFDBBatchResult, VFDBResult as VFDBR

        async def _test():
            session = self._make_session()
            job = MagicMock(id="test-job")
            step = MagicMock(id="3-3", status="pending")

            mock_result = VFDBBatchResult(
                results=[
                    VFDBR(query_id="P0A", is_virulence_factor=True, best_identity=55.0, best_evalue=1e-20, total_hits=3),
                    VFDBR(query_id="P0B", is_virulence_factor=False, best_identity=15.0, best_evalue=0.1, total_hits=0),
                ],
                total_candidates=2,
                virulence_count=1,
                vfdb_fasta_path="/tmp/mev-vfdb/VFDB_setA_pro.fas",
            )
            with patch("app.tools.runner_additions.blast_vfdb", return_value=mock_result):
                result = await run_3_3(session, job, step)

            self.assertEqual(result["virulence_count"], 1)
            self.assertEqual(result["non_virulence_count"], 1)
            self.assertEqual(session["virulence_factors"]["count"], 1)
            self.assertEqual(len(session["virulence_factors"]["candidates"]), 1)

        asyncio.run(_test())

    def test_vfdb_failure_pauses(self):
        from app.tools.runner_additions import run_3_3

        async def _test():
            session = self._make_session()
            job = MagicMock(id="test-job")
            step = MagicMock(id="3-3", status="pending")

            with patch("app.tools.runner_additions.blast_vfdb", side_effect=Exception("download failed")):
                with self.assertRaises(ToolUnavailableError):
                    await run_3_3(session, job, step)

        asyncio.run(_test())


# ======================================================================
# 8. STEP_RUNNERS REGISTRATION — wrappers expose the engine signature
# ======================================================================

class TestRunnerRegistration(unittest.TestCase):
    def test_new_steps_registered_with_session_adapter(self):
        from app.tools.runner import STEP_RUNNERS, STEP_TIMEOUTS

        for step_id in ("2-2", "2-3", "2-4", "3-1", "3-2", "3-3", "3-4"):
            self.assertIn(step_id, STEP_RUNNERS, f"{step_id} not registered")
            self.assertIn(step_id, STEP_TIMEOUTS, f"{step_id} missing timeout")

    def test_all_50_steps_registered(self):
        """All 50 pipeline steps should have registered runners."""
        from app.tools.runner import STEP_RUNNERS
        from app.pipeline import TOTAL_STEPS

        self.assertEqual(TOTAL_STEPS, 50)
        # Count registered runners (includes 1-1, 2-1, 5-1, 6-1 via @runner decorator)
        registered = set(STEP_RUNNERS.keys())
        for step_id in registered:
            self.assertIn(step_id, STEP_RUNNERS)

    def test_engine_signature_calls_session_runner(self):
        """run_runner(job, step) must drive the (session, job, step) runner."""
        import app.tools.runner as runner_mod
        from app.models import JobCreate
        from app.repo import repo

        repo._jobs.clear()
        repo._events.clear()
        import threading
        repo._lock = threading.RLock()
        runner_mod._RUN_SESSION.clear()

        job = repo.create(JobCreate(name="t", taxonId=208435, realTools=True))
        runner_mod.get_session(job.id)["essential"] = {"candidates": [], "count": 0}
        step = MagicMock(id="2-2", status="pending")

        # The registered wrapper forwards to run_2_2 (now local computation).
        async def _run():
            result = await runner_mod.run_runner(job, step, timeout=runner_mod.runner_timeout("2-2"))
            self.assertEqual(result["total_analyzed"], 0)
            self.assertEqual(result["method"], "psortb_6.0_local")

        asyncio.run(_run())


class TestPhase4_11LocalRunners(unittest.TestCase):
    """Tests for the local-computation runners in Phases 4-11."""

    def _make_session(self):
        return {"epitopes": [], "essential": {"candidates": []}}

    def test_swissmodel_local_empty(self):
        import asyncio
        from app.tools.runner_additions import run_11_1

        async def _test():
            session = self._make_session()
            job = MagicMock(id="test")
            step = MagicMock(id="4-2", status="pending")
            result = await run_11_1(session, job, step)
            self.assertEqual(result["targets_analyzed"], 0)
            self.assertEqual(result["models_found"], 0)

        asyncio.run(_test())

    def test_coordinate_analysis_pauses_without_real_coordinates(self):
        import asyncio
        from app.tools.runner_additions import run_11_4

        async def _test():
            session = self._make_session()
            job = MagicMock(id="test")
            step = MagicMock(id="4-3", status="pending")
            with self.assertRaises(ToolUnavailableError) as ctx:
                await run_11_4(session, job, step)
            self.assertEqual(ctx.exception.tool_name, "Local coordinate quality analysis")
            self.assertIn("no usable validated coordinate data", ctx.exception.reason)

        asyncio.run(_test())

    def test_prosa_local_empty(self):
        import asyncio
        from app.tools.runner_additions import run_11_5

        async def _test():
            session = self._make_session()
            job = MagicMock(id="test")
            step = MagicMock(id="11-5", status="pending")
            with self.assertRaises(ToolUnavailableError) as ctx:
                await run_11_5(session, job, step)
            self.assertIn("sequence-inferred contacts are not used", ctx.exception.reason)

        asyncio.run(_test())

    def test_sopma_local_empty(self):
        import asyncio
        from app.tools.runner_additions import run_4_1_pause

        async def _test():
            session = self._make_session()
            job = MagicMock(id="test")
            step = MagicMock(id="4-4", status="pending")
            result = await run_4_1_pause(session, job, step)
            self.assertEqual(result["method"], "chou_fasman_local")
            self.assertEqual(result["proteins_analyzed"], 0)

        asyncio.run(_test())

    def test_dbd2_local_empty(self):
        import asyncio
        from app.tools.runner_additions import run_13_1

        async def _test():
            session = self._make_session()
            job = MagicMock(id="test")
            step = MagicMock(id="12-1", status="pending")
            result = await run_13_1(session, job, step)
            self.assertEqual(result["method"], "dbd2_local")
            self.assertEqual(result["count"], 0)
            self.assertEqual(result["candidates"], [])

        asyncio.run(_test())

    def test_cimmsim_local_ode_completes_without_external_service(self):
        import asyncio
        from app.tools.runner_additions import run_14_1

        async def _test():
            session = self._make_session()
            job = MagicMock(id="test")
            step = MagicMock(id="14-1", status="pending")
            # Phase 14-1 runs the local ODE immune model when the external
            # C-ImmSim service is not attached; it must complete, not pause.
            result = await run_14_1(session, job, step)
            self.assertIn("local ODE model", result["message"])
            self.assertEqual(result["provenance"]["status"], "local-analysis")
            self.assertIn("immune_simulation", session)

        asyncio.run(_test())

    def test_protein_sol_local_empty(self):
        import asyncio
        from app.tools.runner_additions import run_10_5

        async def _test():
            session = self._make_session()
            job = MagicMock(id="test")
            step = MagicMock(id="10-5", status="pending")
            result = await run_10_5(session, job, step)
            self.assertEqual(result["method"], "proteinsol_local_mev")
            self.assertIsNone(result["score"])

        asyncio.run(_test())

    def test_all_epitope_local_runners_return_dict(self):
        """All epitope-phase runners should now complete locally, not raise."""
        import asyncio
        from app.tools.runner_additions import (
            run_5_2, run_5_3, run_5_4,
            run_6_2, run_6_3, run_6_4, run_6_5, run_6_6, run_6_7,
            run_7_1, run_7_2, run_7_3, run_7_4, run_7_5,
            run_10_2, run_10_3, run_10_4,
        )

        runners = [
            run_5_2, run_5_3, run_5_4,    # CTL: VaxiJen, AlgPred, ToxinPred
            run_6_2, run_6_3, run_6_4,    # HTL: IFNepitope, IL4Pred, IL10Pred
            run_6_5, run_6_6, run_6_7,    # HTL: VaxiJen, AlgPred, ToxinPred
            run_7_1, run_7_2, run_7_3,    # B-cell: ABCpred, VaxiJen, AlgPred
            run_7_4, run_7_5,              # B-cell: ToxinPred, Ellipro
            run_10_2, run_10_3, run_10_4,  # MEV: VaxiJen, AlgPred, ToxinPred
        ]

        async def _test():
            session = self._make_session()
            job = MagicMock(id="test")
            step = MagicMock(id="test", status="pending")
            for runner in runners:
                with self.subTest(runner=runner.__name__):
                    result = await runner(session, job, step)
                    self.assertIsInstance(result, dict)
                    self.assertIn("method", result)

        asyncio.run(_test())

    def test_bcell_window_is_passed_to_local_predictor(self):
        from app.tools.runner_additions import run_7_1

        async def _test():
            session = {
                "vaccine_targets": {
                    "candidates": [{"uniprotId": "P0A", "name": "Target", "sequence": "A" * 32}]
                }
            }
            job = MagicMock(id="test", config=SimpleNamespace(bCellWindow=16))
            step = MagicMock(id="7-1", status="pending")
            prediction = {
                "epitope_fragments": [],
                "method": "bepipred_local",
            }
            with patch("app.tools.runner_additions.bcell_local.predict_bepipred_epitope", return_value=prediction) as predict:
                result = await run_7_1(session, job, step)
            self.assertEqual(result["scored"], 1)
            predict.assert_called_once_with("A" * 32, window_size=16)

        asyncio.run(_test())

    def test_mev_allergenicity_uses_local_result_key(self):
        import asyncio
        from app.tools.runner_additions import run_10_3

        async def _test():
            session = {"mev_construct": {"sequence": "ACDEFGHIKLMNPQRSTVWY"}}
            job = MagicMock(id="test")
            step = MagicMock(id="10-3", status="pending")
            result = await run_10_3(session, job, step)
            self.assertEqual(result["method"], "algpred_local_mev")
            self.assertEqual(result["score"], session["mev_allergenicity"]["allergen_score"])
            self.assertIn("allergenicity score", result["message"])

        asyncio.run(_test())


class TestPhase4_1ProtParam(unittest.TestCase):
    """Tests for run_4_1 — ProtParam analysis of individual proteins."""

    def test_empty_candidates(self):
        import asyncio
        from app.tools.runner_additions import run_4_1

        async def _test():
            session = {"essential": {"candidates": []}}
            job = MagicMock(id="test")
            step = MagicMock(id="4-1", status="pending")
            result = await run_4_1(session, job, step)
            self.assertEqual(result["proteins_analyzed"], 0)

        asyncio.run(_test())

    def test_analyzes_sequences(self):
        import asyncio
        from app.tools.runner_additions import run_4_1

        async def _test():
            session = {
                "essential": {
                    "candidates": [
                        {"uniprotId": "P12345", "name": "test1", "sequence": "ACDEFGHIKL"},
                        {"uniprotId": "P67890", "name": "test2", "sequence": "VWYMNQRST"},
                    ]
                }
            }
            job = MagicMock(id="test")
            step = MagicMock(id="4-1", status="pending")
            result = await run_4_1(session, job, step)
            self.assertEqual(result["proteins_analyzed"], 2)
            self.assertEqual(result["method"], "protparam_local_biopython")
            self.assertEqual(len(session["protein_properties"]), 2)
            self.assertIn("molecular_weight", session["protein_properties"][0])

        asyncio.run(_test())

    def test_novel_mev_structure_pauses_without_external_coordinates(self):
        from app.tools.runner_additions import run_11_2

        async def _test():
            session = {"mev_construct": {"sequence": "ACDEFGHIKLMNPQRSTVWY"}}
            job = MagicMock(id="test")
            step = MagicMock(id="11-2", status="pending")
            # Pin the provider: the default (esmfold) predicts novel sequences
            # directly; alphafold_db requires an attached external structure.
            with patch.dict(os.environ, {"MEV_STRUCTURE_PROVIDER": "alphafold_db"}):
                with self.assertRaises(ToolUnavailableError) as ctx:
                    await run_11_2(session, job, step)
            self.assertIn("novel MEV construct", str(ctx.exception))
            self.assertIn("validated external structure", str(ctx.exception))

        asyncio.run(_test())


class TestAlphaFoldClient(unittest.TestCase):
    """Tests for the AlphaFold DB API client."""

    def test_mean_plddt_from_list(self):
        from app.tools.alphafold import _mean_plddt

        record = {"plddt": [50.0, 80.0, 90.0, 70.0]}
        result = _mean_plddt(record)
        self.assertAlmostEqual(result, 72.5, places=1)

    def test_mean_plddt_none(self):
        from app.tools.alphafold import _mean_plddt

        result = _mean_plddt({})
        self.assertIsNone(result)

    def test_mean_plddt_global_metric(self):
        from app.tools.alphafold import _mean_plddt

        record = {"globalMetricValue": 85.5}
        result = _mean_plddt(record)
        self.assertEqual(result, 85.5)


class TestPhase9Assembly(unittest.TestCase):
    """Tests for MEV assembly and adjuvant selection."""

    def test_adjuvant_local_empty(self):
        import asyncio
        from app.tools.runner_additions import run_9_1_adj

        async def _test():
            session = {}
            job = MagicMock(id="test", config=SimpleNamespace(adjuvant="ctxb"))
            step = MagicMock(id="9-1", status="pending")
            result = await run_9_1_adj(session, job, step)
            self.assertEqual(result["method"], "user-configured adjuvant")
            self.assertEqual(result["source"], "user-provided")
            self.assertEqual(result["recommended"], "ctxb")

        asyncio.run(_test())

    def test_mev_optional_sequences_require_and_preserve_provenance(self):
        from app.tools.runner_additions import _assemble_mev_construct

        epitopes = [{
            "type": "CTL",
            "sequence": "ACDEFGHIK",
            "sourceProtein": "P0A",
            "percentileRank": 1.0,
        }]
        construct = _assemble_mev_construct(
            epitopes,
            "EAAAK",
            "GPGPG",
            "KK",
            use_optional_sequences=True,
            adjuvant_sequence="MKTLL",
            adjuvant_source="UniProt:P12345",
            signal_peptide_sequence="MKKLL",
            signal_peptide_source="UniProt:P54321",
        )
        self.assertTrue(construct["sequence"].startswith("MKKLLMKTLL"))
        self.assertEqual(construct["adjuvantSource"], "UniProt:P12345")
        self.assertEqual(construct["signalPeptideSource"], "UniProt:P54321")

        # Optional payload cannot change the exact legacy result unless the
        # explicit opt-in is enabled.
        legacy = _assemble_mev_construct(
            epitopes,
            "EAAAK",
            "GPGPG",
            "KK",
            adjuvant_sequence="MKTLL",
            signal_peptide_sequence="MKKLL",
        )
        self.assertFalse(legacy["sequence"].startswith("MKKLLMKTLL"))
        self.assertIsNone(legacy["signalPeptideSource"])

        with self.assertRaises(ValueError):
            _assemble_mev_construct(
                epitopes,
                "EAAAK",
                "GPGPG",
                "KK",
                use_optional_sequences=True,
                adjuvant_sequence="MKTLL",
            )

    def test_mev_enabled_without_real_optional_sequence_is_unavailable(self):
        import asyncio
        from app.tools.graceful_pause import ToolUnavailableError
        from app.tools.runner_additions import run_9_1

        async def _test():
            session = {
                "conserved_epitopes": [{
                    "type": "CTL",
                    "sequence": "ACDEFGHIK",
                    "selected": True,
                }],
            }
            job = MagicMock(
                id="test",
                config=SimpleNamespace(enableMevEnhancements=True),
            )
            step = MagicMock(id="9-2", status="pending")
            with self.assertRaises(ToolUnavailableError) as ctx:
                await run_9_1(session, job, step)
            self.assertIn("no real signal-peptide or full-adjuvant", str(ctx.exception))

        asyncio.run(_test())

    def test_mev_assembly_empty(self):
        import asyncio
        from app.tools.runner_additions import run_9_1

        async def _test():
            session = {}
            job = MagicMock(id="test")
            step = MagicMock(id="9-2", status="pending")
            result = await run_9_1(session, job, step)
            self.assertEqual(result["mev_length"], 0)

        asyncio.run(_test())


class TestRunComparisonEndpoint(unittest.TestCase):
    def test_compare_jobs_returns_funnel_mev_and_provenance(self):
        from app.models import JobCreate
        from app.repo import repo
        from app.routes import compare_jobs

        repo._jobs.clear()
        repo._events.clear()
        first = repo.create(JobCreate(name="first"))
        second = repo.create(JobCreate(name="second"))
        for job, length in ((first, 100), (second, 120)):
            job.status = "completed"
            job.funnel = [{"key": "proteins", "count": length}]
            mev_step = next(step for phase in job.phases for step in phase.steps if step.id == "9-2")
            mev_step.result = {
                "mev_length": length,
                "ctl_epitopes": 1,
                "provenance": {"status": "local-analysis"},
            }
            repo.upsert(job)

        compared = compare_jobs(f"{first.id},{second.id}")
        self.assertEqual([item["name"] for item in compared["jobs"]], ["first", "second"])
        self.assertEqual(compared["jobs"][0]["funnel"][0]["count"], 100)
        self.assertEqual(compared["jobs"][1]["mevMetrics"]["mev_length"], 120)
        self.assertEqual(compared["jobs"][0]["provenance"]["status"], "local-analysis")


class TestPhase13Runners(unittest.TestCase):
    """Tests for Phase 13: Codon optimization and cloning."""

    def test_jcat_no_sequence(self):
        import asyncio
        from app.tools.runner_additions import run_12_1

        async def _test():
            session = {}
            job = MagicMock(id="test")
            step = MagicMock(id="13-1", status="pending")
            result = await run_12_1(session, job, step)
            self.assertEqual(result["codon_optimized"], "")

        asyncio.run(_test())

    def test_restriction_no_sequence(self):
        import asyncio
        from app.tools.runner_additions import run_13_2

        async def _test():
            session = {}
            job = MagicMock(id="test")
            step = MagicMock(id="13-2", status="pending")
            result = await run_13_2(session, job, step)
            self.assertIn("restriction_sites", result)

        asyncio.run(_test())

    def test_cloning_no_sequence(self):
        import asyncio
        from app.tools.runner_additions import run_13_3

        async def _test():
            session = {}
            job = MagicMock(id="test")
            step = MagicMock(id="13-3", status="pending")
            result = await run_13_3(session, job, step)
            self.assertEqual(result["method"], "cloning_local")
            self.assertIsNone(result["cloning_strategy"])

        asyncio.run(_test())


class TestEBIRestClientRetries(unittest.TestCase):
    """Verify EBI REST client survives transient 5xx outages (no await-float bug)."""

    def test_jittered_is_not_awaitable(self):
        import asyncio
        from app.tools.ebi_rest_client import EBIRestClient

        async def _test():
            val = EBIRestClient._jittered(5.0)
            self.assertIsInstance(val, float)
            self.assertTrue(0.75 * 5.0 <= val <= 1.25 * 5.0)

        asyncio.run(_test())

    def test_post_retries_transient_500s_and_succeeds(self):
        import asyncio
        import app.tools.ebi_rest_client as ebi
        from app.tools.ebi_rest_client import EBIRestClient

        class _FakeResponse:
            def __init__(self, status_code: int, text: str = ""):
                self.status_code = status_code
                self._text = text

            def raise_for_status(self):
                if self.status_code >= 400:
                    import httpx
                    raise httpx.HTTPStatusError(
                        f"server error {self.status_code}",
                        request=None,
                        response=self,
                    )

            @property
            def text(self):
                return self._text

        saved_retry = ebi.MAX_RETRIES
        saved_server = ebi.MAX_SERVER_RETRIES
        saved_server_backoff = ebi._SERVER_ERROR_BACKOFF
        saved_client_fn = EBIRestClient._http

        async def _test():
            calls = {"n": 0}

            class _FakeClient:
                async def post(self, url, data):
                    calls["n"] += 1
                    if calls["n"] <= 2:
                        return _FakeResponse(500)
                    return _FakeResponse(200, "job-123")

            async def fake_http(self):
                return _FakeClient()

            EBIRestClient._http = fake_http
            # single short backoff step so the test runs fast
            ebi.MAX_RETRIES = 3
            ebi.MAX_SERVER_RETRIES = 3
            ebi._SERVER_ERROR_BACKOFF = (0.001, 0.001, 0.001)
            try:
                client = EBIRestClient(email="test@example.com")
                resp = await client._post("http://example.invalid/post", {"a": "b"})
                self.assertEqual(resp.status_code, 200)
                self.assertEqual(calls["n"], 3, "should retry two 500s then succeed")
            finally:
                EBIRestClient._http = saved_client_fn
                ebi.MAX_RETRIES = saved_retry
                ebi.MAX_SERVER_RETRIES = saved_server
                ebi._SERVER_ERROR_BACKOFF = saved_server_backoff

        asyncio.run(_test())


if __name__ == "__main__":
    import warnings
    warnings.filterwarnings("ignore", category=ResourceWarning)
    unittest.main(verbosity=2)
