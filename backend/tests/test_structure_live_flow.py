from __future__ import annotations

import pytest

from app.models import JobCreate, MEVStructureInput
from app.repo import repo
from app.routes import attach_structure_model, structure_requirements
from app.simulator import engine
from app.tools.runner import clear_session, get_session


def _pdb() -> str:
    names = {"A": "ALA", "V": "VAL", "L": "LEU", "G": "GLY"}
    lines: list[str] = []
    serial = 1
    for residue_number, aa in enumerate("AVLG", 1):
        x = (residue_number - 1) * 3.8
        for atom, dx, dy, element in (("N", -1.3, 1.0, "N"), ("CA", 0.0, 0.0, "C"), ("C", 1.3, 1.0, "C")):
            lines.append(f"ATOM  {serial:5d} {atom:>4} {names[aa]:>3} A{residue_number:4d}    {x + dx:8.3f}{dy:8.3f}{0.0:8.3f}  1.00 90.00           {element:>2}")
            serial += 1
    return "\n".join(lines + ["END"]) + "\n"


@pytest.mark.asyncio
async def test_live_attachment_route_retry_and_runner_share_backend_session() -> None:
    from app.routes import retry_step

    job = repo.create(JobCreate(name="live structure flow", taxonId=1))
    try:
        for phase in job.phases:
            for step in phase.steps:
                if step.id == "9-2":
                    step.status = "success"
                    step.result = {"sequence": "AVLG"}
                elif step.id == "11-2":
                    step.status = "paused"
                elif int(step.id.split("-")[0]) < 11 or step.id == "11-1":
                    step.status = "success"
        job.status = "paused"
        repo.upsert(job)
        get_session(job.id)["mev_construct"] = {"sequence": "AVLG"}

        requirements = structure_requirements(job.id)
        assert requirements["attachmentStatus"] == "missing"
        attached = attach_structure_model(job.id, MEVStructureInput(
            sequence=requirements["sequence"], coordinateText=_pdb(), modelFormat="pdb",
            provider="user-uploaded PDB", method="exact MEV PDB", fileName="mev.pdb",
        ))
        assert attached["status"] == "attached"
        assert attached["backendInstanceId"] == requirements["backendInstanceId"]
        assert structure_requirements(job.id)["attachmentStatus"] == "attached"

        queued = await retry_step(job.id, "11-2")
        assert queued.status == "running"
        await engine._advance(repo.get(job.id))
        persisted = repo.get(job.id)
        step = next(s for phase in persisted.phases for s in phase.steps if s.id == "11-2")
        assert step.status == "success"
        assert get_session(job.id)["validated_coordinate_data"][0]["sequence"] == "AVLG"
    finally:
        clear_session(job.id)
        repo.delete(job.id)


@pytest.mark.asyncio
async def test_resume_requeues_paused_real_structure_step_after_attachment() -> None:
    from app.routes import resume_job

    job = repo.create(JobCreate(name="resume structure flow", taxonId=1))
    try:
        for phase in job.phases:
            for step in phase.steps:
                if step.id == "9-2":
                    step.status = "success"
                    step.result = {"sequence": "AVLG"}
                elif step.id == "11-2":
                    step.status = "paused"
                elif int(step.id.split("-")[0]) < 11 or step.id == "11-1":
                    step.status = "success"
        job.status = "paused"
        repo.upsert(job)
        get_session(job.id)["mev_construct"] = {"sequence": "AVLG"}
        attach_structure_model(job.id, MEVStructureInput(sequence="AVLG", coordinateText=_pdb(), modelFormat="pdb"))

        resumed = await resume_job(job.id)
        assert resumed.status == "running"
        assert next(s for p in resumed.phases for s in p.steps if s.id == "11-2").status == "pending"
        await engine._advance(repo.get(job.id))
        assert next(s for p in repo.get(job.id).phases for s in p.steps if s.id == "11-2").status == "success"
    finally:
        clear_session(job.id)
        repo.delete(job.id)
