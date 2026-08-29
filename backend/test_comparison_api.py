import unittest

from fastapi.testclient import TestClient

from app.main import app
from app.models import JobCreate
from app.repo import repo


class ComparisonApiTest(unittest.TestCase):
    def setUp(self):
        repo._jobs.clear()
        repo._events.clear()

    def test_compare_is_additive_aligned_and_honest_about_provenance(self):
        first = repo.create(JobCreate(name="first"))
        second = repo.create(JobCreate(name="second"))
        for job in (first, second):
            job.status = "completed"
        first.funnel = [
            {"key": "proteins", "label": "Proteins", "count": 10},
            {"key": "essential", "label": "Essential", "count": 4},
        ]
        second.funnel = [
            {"key": "proteins", "label": "Proteins", "count": 12},
            {"key": "surface", "label": "Surface", "count": 3},
        ]
        first_step = next(step for phase in first.phases for step in phase.steps if step.id == "9-2")
        first_step.result = {"mev_length": 300, "provenance": {"status": "local-analysis"}}
        second_step = next(step for phase in second.phases for step in phase.steps if step.id == "9-2")
        second_step.result = {"mev_length": 620, "provenance": {"status": "unavailable"}}
        repo.upsert(first)
        repo.upsert(second)

        with TestClient(app) as client:
            response = client.get(f"/api/jobs/compare?ids={first.id},{second.id}")

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual([stage["key"] for stage in data["funnelStages"]], ["proteins", "essential", "surface"])
        self.assertEqual(data["jobs"][0]["mevMetrics"]["mev_length"], 300)
        self.assertEqual(data["jobs"][0]["provenance"]["status"], "local-analysis")
        self.assertEqual(data["jobs"][1]["mevMetrics"], {})
        self.assertEqual(data["jobs"][1]["provenance"]["status"], "unavailable")

    def test_compare_rejects_incomplete_jobs(self):
        first = repo.create(JobCreate(name="first"))
        second = repo.create(JobCreate(name="second"))
        first.status = "completed"
        repo.upsert(first)
        with TestClient(app) as client:
            response = client.get(f"/api/jobs/compare?ids={first.id},{second.id}")
        self.assertEqual(response.status_code, 409)


if __name__ == "__main__":
    unittest.main()
