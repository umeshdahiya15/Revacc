from __future__ import annotations

import pytest

from app.models import JobCreate, Step
from app.repo import repo
from app.simulator import _result_provenance
from app.tools import runner as runner_mod


def _reset() -> None:
    repo._jobs.clear()
    repo._events.clear()
    runner_mod._RUN_SESSION.clear()


def test_uniprot_provenance_is_not_replaced_by_generic_status() -> None:
    step = Step(id="1-1", phase=1, number=1, name="Retrieve", tool="UniProt")
    result = {
        "source": "https://rest.uniprot.org/uniprotkb/stream",
        "sourceType": "cached-real",
        "provenance": {
            "cacheType": "cached-real",
            "source": "https://rest.uniprot.org/uniprotkb/stream",
            "query": "proteome:UP-COMPLETE",
            "proteomeId": "UP-COMPLETE",
            "referenceProteomeProteinCount": 2,
            "returnedRecordCount": 2,
        },
    }

    provenance = _result_provenance(result, step)

    assert provenance["status"] == "cached-real"
    assert provenance["source"].endswith("/uniprotkb/stream")
    assert provenance["query"] == "proteome:UP-COMPLETE"
    assert provenance["referenceProteomeProteinCount"] == 2
    assert provenance["returnedRecordCount"] == 2


@pytest.mark.asyncio
async def test_redundancy_step_refuses_to_refetch_without_exact_retrieved_session() -> None:
    _reset()
    job = repo.create(JobCreate(name="session integrity", taxonId=208435))
    step = next(step for phase in job.phases for step in phase.steps if step.id == "1-2")

    with pytest.raises(RuntimeError, match="refusing to refetch"):
        await runner_mod.runner_remove_redundants(job, step)
