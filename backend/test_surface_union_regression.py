from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from app.tools.graceful_pause import ToolUnavailableError
from app.tools.runner_additions import run_2_4


class SurfaceUnionRegressionTest(unittest.TestCase):
    def _job_step(self):
        return MagicMock(id="job"), MagicMock(id="2-4", status="pending")

    def _candidates(self):
        return [
            {"index": 0, "uniprotId": "PSORT", "name": "wall", "sequence": "M" * 80},
            {"index": 1, "uniprotId": "PHOBIUS", "name": "secreted", "sequence": "A" * 80},
        ]

    def test_real_psortb_result_survives_phobius_outage(self):
        async def exercise():
            candidates = self._candidates()
            session = {
                "essential": {"candidates": candidates, "count": 2},
                "surface_exposed": {"candidates": [candidates[0]], "count": 1},
                "psortb": {
                    "surface_candidate_ids": ["PSORT"],
                    "provenance": {"status": "real"},
                },
            }
            job, step = self._job_step()
            client = AsyncMock()
            client.submit = AsyncMock(side_effect=ToolUnavailableError("Phobius", "down", "retry"))
            client.close = AsyncMock()
            with patch("app.tools.runner_additions.EBIRestClient", return_value=client), \
                 patch("app.tools.runner_additions.PHOBIUS_RATE_LIMIT_SEC", 0.0), \
                 patch("app.tools.runner_additions._load_phobius_cache", return_value={}), \
                 patch("app.tools.runner_additions._save_phobius_cache"):
                result = await run_2_4(session, job, step)
            self.assertEqual(result["surface_exposed_count"], 1)
            self.assertEqual(session["surface_exposed"]["count"], 1)
            self.assertEqual(session["_funnel_counts"]["surface_exposed"], 1)
            self.assertEqual(result["status"], "partial")
            self.assertEqual(result["provenance"]["status"], "partial")
            self.assertEqual(session["surface_exposed"]["candidates"][0]["uniprotId"], "PSORT")

        asyncio.run(exercise())

    def test_psortb_and_valid_phobius_results_are_unioned(self):
        async def exercise():
            candidates = self._candidates()
            session = {
                "essential": {"candidates": candidates, "count": 2},
                "surface_exposed": {"candidates": [candidates[0]], "count": 1},
                "psortb": {"surface_candidate_ids": ["PSORT"]},
            }
            job, step = self._job_step()
            client = AsyncMock()
            client.submit = AsyncMock(side_effect=["job-psort", "job-phobius"])
            client.poll_status = AsyncMock(return_value="FINISHED")
            client.fetch_result = AsyncMock(return_value="ID X\nFT   SIGNAL      1      24\n//")
            client.close = AsyncMock()
            with patch("app.tools.runner_additions.EBIRestClient", return_value=client), \
                 patch("app.tools.runner_additions.PHOBIUS_RATE_LIMIT_SEC", 0.0), \
                 patch("app.tools.runner_additions._load_phobius_cache", return_value={}), \
                 patch("app.tools.runner_additions._save_phobius_cache"):
                result = await run_2_4(session, job, step)
            self.assertEqual(result["surface_exposed_count"], 2)
            self.assertEqual({c["uniprotId"] for c in session["surface_exposed"]["candidates"]}, {"PSORT", "PHOBIUS"})

        asyncio.run(exercise())


if __name__ == "__main__":
    unittest.main()
