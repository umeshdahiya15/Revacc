import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.tools.ebi_rest_client import (
    PSORTbClient,
    classify_psortb,
    is_psortb_surface,
    parse_psortb_out,
)


class TestPSORTbClient(unittest.TestCase):
    def test_parser_classifies_surface_categories(self) -> None:
        raw = "\n".join(
            [
                "SeqID\tLocalization\tScore",
                "outer\tOuterMembrane\t0.90",
                "lipo\tLipoprotein\t0.80",
                "wall\tCell-wall-anchored\t0.70",
                "extra\tExtracellular\t0.60",
                "inside\tCytoplasmic\t0.10",
            ]
        )
        parsed = parse_psortb_out(raw)

        self.assertEqual(parsed["outer"]["category"], "outer_membrane")
        self.assertEqual(parsed["lipo"]["category"], "lipoprotein")
        self.assertEqual(parsed["wall"]["category"], "cell_wall_anchored")
        self.assertEqual(parsed["extra"]["category"], "extracellular")
        self.assertTrue(is_psortb_surface("Outer membrane"))
        self.assertTrue(is_psortb_surface("Lipoprotein"))
        self.assertTrue(is_psortb_surface("Cell Wall Anchored"))
        self.assertTrue(is_psortb_surface("Extracellular"))
        self.assertFalse(is_psortb_surface("Cytoplasmic"))

    def test_parser_accepts_whitespace_output(self) -> None:
        parsed = parse_psortb_out("seq_0 Outer membrane 0.91\nseq_1 Cytoplasmic 0.05\n")
        self.assertEqual(parsed["seq_0"]["category"], "outer_membrane")
        self.assertEqual(parsed["seq_0"]["score"], 0.91)
        self.assertEqual(classify_psortb("cell-wall-anchored"), "cell_wall_anchored")

    def test_client_runs_configured_binary_and_aligns_results(self) -> None:
        completed = SimpleNamespace(
            returncode=0,
            stdout="seq_0\tLipoprotein\t0.8\nseq_1\tCytoplasmic\t0.1\n",
            stderr="",
        )
        with patch("app.tools.ebi_rest_client.subprocess.run", return_value=completed) as run:
            results = __import__("asyncio").run(
                PSORTbClient(binary="/configured/psortb").localize(["MAAA", "MCCC"])
            )

        self.assertEqual(results[0]["category"], "lipoprotein")
        self.assertEqual(results[1]["category"], "intracellular")
        command = run.call_args.args[0]
        self.assertEqual(command[0], "/configured/psortb")
        self.assertIn("--output", command)
        self.assertIn("terse", command)
    def test_runner_prefers_configured_binary_and_records_surface_ids(self) -> None:
        from app.tools import runner_additions

        candidates = [
            {"index": 0, "uniprotId": "OUTER", "sequence": "MAAA"},
            {"index": 1, "uniprotId": "INTRA", "sequence": "MCCC"},
        ]
        session = {"essential": {"candidates": candidates}}
        fake_client = AsyncMock()
        fake_client.localize.return_value = [
            {"localization": "OuterMembrane", "score": 0.9},
            {"localization": "Cytoplasmic", "score": 0.1},
        ]
        with patch.object(runner_additions, "find_psortb_binary", return_value="/configured/psortb"), \
             patch.object(runner_additions, "PSORTbClient", return_value=fake_client):
            result = asyncio.run(runner_additions.run_2_2(session, None, None))

        self.assertEqual(result["method"], "psortb_6.0_local")
        self.assertEqual(result["surface_exposed_count"], 1)
        self.assertEqual(
            [c["uniprotId"] for c in result["classifications"] if c["surface_exposed"]],
            ["OUTER"],
        )
        self.assertEqual(session["psortb"]["surface_candidate_ids"], ["OUTER"])
    def test_missing_psortb_continues_with_phobius_and_residual(self) -> None:
        from app.tools import psortb_docker, runner_additions

        candidates = [{"index": 0, "uniprotId": "SECRETED", "sequence": "MSSS"}]
        session = {"essential": {"candidates": candidates}}
        unavailable = psortb_docker.PSORTbUnavailable("binary missing")
        with patch.object(runner_additions, "find_psortb_binary", return_value=None), \
             patch.object(psortb_docker, "require_available", side_effect=unavailable):
            psortb_result = asyncio.run(runner_additions.run_2_2(session, None, None))

        self.assertEqual(psortb_result["status"], "partial")
        self.assertFalse(psortb_result["provenance"]["psortbAvailable"])
        self.assertIn("binary missing", psortb_result["residual_gap"])

        phobius = AsyncMock()
        phobius.submit.return_value = "job-1"
        phobius.poll_status.return_value = "FINISHED"
        phobius.fetch_result.return_value = "ID X\nFT   SIGNAL      1      3\n//"
        with patch.object(runner_additions, "EBIRestClient", return_value=phobius), \
             patch.object(runner_additions, "PHOBIUS_RATE_LIMIT_SEC", 0.0), \
             patch.object(runner_additions, "_load_phobius_cache", return_value={}), \
             patch.object(runner_additions, "_save_phobius_cache"):
            result = asyncio.run(runner_additions.run_2_4(session, None, None))

        self.assertEqual(result["surface_exposed_count"], 1)
        self.assertTrue(result["provenance"]["psortbAvailable"] is False)
        self.assertIn("binary missing", result["provenance"]["residualGap"])


if __name__ == "__main__":
    unittest.main()
