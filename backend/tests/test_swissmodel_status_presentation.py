"""Task 20.7 API presentation and preservation tests using local fixtures only."""
from __future__ import annotations

import pytest

from app.models import JobCreate, MEVStructureInput, StepError
from app.repo import repo
from app.routes import attach_structure_model, get_job, list_jobs, structure_requirements
from app.tools.runner import clear_session

from .swissmodel_contract_fixtures import assert_public_sinks_safe


_FINGERPRINT = "a" * 64


def _step(job, step_id: str):
    return next(step for phase in job.phases for step in phase.steps if step.id == step_id)


def _official_lifecycle(state: str) -> dict[str, object]:
    marker = "sensitive-fixture-marker"
    return {
        "status": state,
        "provider": "swissmodel",
        "messageCode": "swissmodel_provider_terminal_failure" if state == "failed" else None,
        "message": marker,
        "action": marker,
        "method": "automodel",
        "selectionId": "selection-1",
        "requestId": "request-42",
        "modelId": "model-7",
        "modelUrl": "https://swissmodel.expasy.org/project/request-42/model/model-7/pdb/",
        "mevFingerprint": _FINGERPRINT,
        "submittedAt": "2025-01-02T03:04:05+00:00",
        "updatedAt": "not-a-timestamp",
        "rawProviderPayload": {"detail": marker},
        "headers": {"authorization": marker},
        "stackTrace": marker,
    }


@pytest.mark.parametrize(
    ("state", "step_status"),
    [
        ("queued", "running"),
        ("running", "running"),
        ("paused", "paused"),
        ("failed", "failed"),
        ("succeeded", "success"),
    ],
)
def test_single_run_api_projects_every_official_lifecycle_state_without_sensitive_data(
    state: str,
    step_status: str,
) -> None:
    """List and single-run routes return only allowlisted lifecycle fields."""
    job = repo.create(JobCreate(name=f"Official status {state}", taxonId=1))
    try:
        step = _step(job, "11-2")
        step.status = step_status  # type: ignore[assignment]
        step.result = {
            "officialLifecycle": _official_lifecycle(state),
            "rawProviderPayload": "sensitive-fixture-marker",
            "requestBody": "sensitive-fixture-marker",
        }
        step.error = StepError(
            message="sensitive-fixture-marker",
            retries=2,
            severity="pause",
            tool="untrusted provider detail",
        )
        repo.upsert(job)

        public_job = get_job(job.id)
        listed_job = next(candidate for candidate in list_jobs() if candidate.id == job.id)
        public_step = _step(public_job, "11-2")
        listed_step = _step(listed_job, "11-2")
        lifecycle = public_step.result["officialLifecycle"]

        assert lifecycle["status"] == state
        assert lifecycle["provider"] == "swissmodel"
        assert lifecycle["method"] == "automodel"
        assert lifecycle["requestId"] == "request-42"
        assert lifecycle["modelId"] == "model-7"
        assert lifecycle["modelUrl"] == "https://swissmodel.expasy.org/project/request-42/model/model-7/pdb/"
        assert lifecycle["mevFingerprint"] == _FINGERPRINT
        assert lifecycle["submittedAt"] == "2025-01-02T03:04:05+00:00"
        assert "updatedAt" not in lifecycle
        assert lifecycle["message"] != "sensitive-fixture-marker"
        assert lifecycle["action"] != "sensitive-fixture-marker"
        assert public_step.result == {"officialLifecycle": lifecycle}
        assert public_step.error is not None
        assert public_step.error.message == lifecycle["message"]
        assert public_step.error.tool == "SWISS-MODEL"
        assert listed_step.result == public_step.result
        assert_public_sinks_safe(
            public_job.model_dump(mode="json"),
            listed_job.model_dump(mode="json"),
        )
    finally:
        clear_session(job.id)
        repo.delete(job.id)


def test_manual_attachment_and_legacy_single_run_data_remain_independent_of_status_presentation() -> None:
    """A manual PDB flow and non-official result fields retain their public behavior."""
    job = repo.create(JobCreate(name="Manual fallback remains available", taxonId=1))
    try:
        assembly = _step(job, "9-2")
        assembly.status = "success"
        assembly.result = {"sequence": "AVLG"}
        official_step = _step(job, "11-2")
        official_step.status = "paused"
        official_step.result = {"officialLifecycle": _official_lifecycle("paused")}
        repo.upsert(job)

        requirements = structure_requirements(job.id)
        attachment = attach_structure_model(
            job.id,
            MEVStructureInput(
                sequence="AVLG",
                coordinateText="ATOM local fixture\nEND\n",
                modelFormat="pdb",
                provider="user-uploaded PDB",
                method="manual fixture attachment",
            ),
        )
        public_job = get_job(job.id)

        assert requirements["sequence"] == "AVLG"
        assert attachment["status"] == "attached"
        assert attachment["provider"] == "user-uploaded PDB"
        assert _step(public_job, "9-2").result == {"sequence": "AVLG"}
        assert _step(public_job, "11-2").result["officialLifecycle"]["status"] == "paused"
        assert "coordinateText" not in str(public_job.model_dump(mode="json"))
    finally:
        clear_session(job.id)
        repo.delete(job.id)
