from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.tools import alphafold, runner_additions
from app.tools.graceful_pause import ToolUnavailableError


def _session() -> dict:
    return {
        "vaccine_targets": {
            "candidates": [
                {
                    "index": 7,
                    "uniprotId": "P12345",
                    "name": "Fixture target",
                    "sequence": "M" + "ACDEFGHIKLMNPQRSTVWY" * 2,
                }
            ]
        }
    }


def _af_result() -> alphafold.AFDBResult:
    entry = alphafold.AFDBEntry(
        uniprot_id="P12345",
        gene="fixture_gene",
        organism="Fixture bacterium",
        sequence="M" + "ACDEFGHIKLMNPQRSTVWY" * 2,
        pdb_url="https://files.rcsb.org/fixture-P12345.pdb",
        cif_url="https://alphafold.ebi.ac.uk/files/AF-P12345-F1-model_v4.cif",
        bcif_url="https://alphafold.ebi.ac.uk/files/AF-P12345-F1-model_v4.bcif",
        plddt=88.5,
        version=4,
        tool_used="AlphaFold",
        entry_id="AF-P12345-F1",
        raw={},
    )
    return alphafold.AFDBResult(found=True, entry=entry, message="fixture model")


@pytest.mark.asyncio
async def test_step_4_2_uses_configured_alphafold_provider_and_preserves_provenance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MEV_STRUCTURE_PROVIDER", "alphafold_db")
    fetch = AsyncMock(return_value=_af_result())
    pdb = AsyncMock(return_value="ATOM fixture\n")
    monkeypatch.setattr(alphafold, "fetch_prediction", fetch)
    monkeypatch.setattr(alphafold, "fetch_structure_pdb", pdb)

    session = _session()
    result = await runner_additions.run_4_2_structure(session, None, None)

    assert result["method"] == "alphafold_db_real_alternate"
    assert result["models_found"] == 1
    assert result["provenance"]["provider"] == "alphafold_db"
    assert result["provenance"]["status"] == "real"
    structure = session["structures"]["structures"][0]
    assert structure["candidateIndex"] == 7
    assert structure["uniprotId"] == "P12345"
    assert structure["pdbUrl"] == "https://files.rcsb.org/fixture-P12345.pdb"
    assert structure["cifUrl"] == "https://alphafold.ebi.ac.uk/files/AF-P12345-F1-model_v4.cif"
    assert structure["bcifUrl"] == "https://alphafold.ebi.ac.uk/files/AF-P12345-F1-model_v4.bcif"
    assert structure["pdbAvailable"] is True
    assert structure["mmCifAvailable"] is True
    fetch.assert_awaited_once_with("P12345")
    pdb.assert_awaited_once_with("https://files.rcsb.org/fixture-P12345.pdb")


@pytest.mark.asyncio
async def test_step_4_2_pauses_when_alpha_fold_has_no_real_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MEV_STRUCTURE_PROVIDER", "alphafold_db")
    monkeypatch.setattr(
        alphafold,
        "fetch_prediction",
        AsyncMock(
            return_value=alphafold.AFDBResult(
                found=False, entry=None, message="No AlphaFold model for P12345"
            )
        ),
    )

    session = _session()
    with pytest.raises(ToolUnavailableError) as exc_info:
        await runner_additions.run_4_2_structure(session, None, None)

    assert exc_info.value.tool_name == "AlphaFold DB"
    assert "no usable PDB/mmCIF" in exc_info.value.reason
    assert session["structures"]["models_found"] == 0
    assert session["structures"]["structures"][0]["status"] == "unavailable"
    assert session["structures"]["provenance"]["status"] == "unavailable"


@pytest.mark.asyncio
async def test_explicit_swissmodel_provider_pauses_without_mislabeling_alphafold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MEV_STRUCTURE_PROVIDER", "swissmodel")
    monkeypatch.delenv("SWISSMODEL_API_TOKEN", raising=False)
    fetch = AsyncMock()
    monkeypatch.setattr(alphafold, "fetch_prediction", fetch)

    with pytest.raises(ToolUnavailableError) as exc_info:
        await runner_additions.run_4_2_structure(_session(), None, None)

    assert exc_info.value.tool_name == "SWISS-MODEL"
    assert "no SWISSMODEL_API_TOKEN" in exc_info.value.reason
    fetch.assert_not_awaited()


@pytest.mark.asyncio
async def test_alphafold_alternate_result_never_calls_itself_swissmodel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MEV_STRUCTURE_PROVIDER", raising=False)
    monkeypatch.setattr(alphafold, "fetch_prediction", AsyncMock(return_value=_af_result()))
    monkeypatch.setattr(alphafold, "fetch_structure_pdb", AsyncMock(return_value=None))

    result = await runner_additions.run_4_2_structure(_session(), None, None)
    serialized = str(result).lower()

    assert "alphafold db" in serialized
    assert "swissmodel" not in serialized
    assert "swiss-model" not in serialized


def _coordinate_pdb() -> str:
    residues = [("ALA", 0.0), ("VAL", 3.8), ("LEU", 7.6), ("GLY", 3.0)]
    return "\n".join(
        f"ATOM  {index:5d}  CA  {name:>3} A{index:4d}    {x:8.3f}{0.0:8.3f}{0.0:8.3f}  1.00 90.00           C"
        for index, (name, x) in enumerate(residues, start=1)
    ) + "\nEND\n"


@pytest.mark.asyncio
async def test_step_4_3_scores_contacts_from_transient_real_afdb_coordinates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MEV_STRUCTURE_PROVIDER", "alphafold_db")
    monkeypatch.setattr(alphafold, "fetch_prediction", AsyncMock(return_value=_af_result()))
    monkeypatch.setattr(alphafold, "fetch_structure_pdb", AsyncMock(return_value=_coordinate_pdb()))

    session = _session()
    await runner_additions.run_4_2_structure(session, None, None)
    result = await runner_additions.run_4_3_coordinate_analysis(session, None, None)

    assert result["analyzed_count"] == 1
    assert result["contacts_analyzed"] > 0
    assert result["source"] == "local-analysis"
    assert result["method"] == "quality_local_coordinate_contact_analysis"
    assert result["provenance"]["provider"] == "alphafold_db"
    assert result["provenance"]["coordinateSource"] == "AlphaFold DB PDB coordinates"
    assert result["provenance"]["officialValidation"] is False
    assert "not official ERRAT" in result["provenance"]["analysisLabel"]
    assert "coordinateText" not in str(result)
    assert "coordinateText" not in str(session["structures"])
    assert session["validated_coordinate_data"][0]["coordinateText"].startswith("ATOM")


@pytest.mark.asyncio
async def test_step_4_3_pauses_when_afdb_has_no_downloadable_coordinate_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MEV_STRUCTURE_PROVIDER", "alphafold_db")
    monkeypatch.setattr(alphafold, "fetch_prediction", AsyncMock(return_value=_af_result()))
    monkeypatch.setattr(alphafold, "fetch_structure_pdb", AsyncMock(return_value=None))

    session = _session()
    await runner_additions.run_4_2_structure(session, None, None)
    with pytest.raises(ToolUnavailableError) as exc_info:
        await runner_additions.run_4_3_coordinate_analysis(session, None, None)

    assert exc_info.value.tool_name == "Local coordinate quality analysis"
    assert "full, parseable PDB coordinates" in exc_info.value.reason
    assert "sequence-inferred" in exc_info.value.reason.lower()
    assert "synthetic" in exc_info.value.reason


@pytest.mark.asyncio
async def test_step_4_3_never_infers_contacts_from_sequence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = {
        "structures": {
            "structures": [{"status": "available", "sequence": "M" + "A" * 20}]
        }
    }
    infer = AsyncMock(side_effect=AssertionError("sequence contact inference must not run"))
    monkeypatch.setattr(runner_additions.quality_local, "_infer_contacts", infer)

    with pytest.raises(ToolUnavailableError) as exc_info:
        await runner_additions.run_4_3_coordinate_analysis(session, None, None)

    infer.assert_not_awaited()
    assert "no usable validated coordinate data" in exc_info.value.reason


@pytest.mark.asyncio
async def test_step_11_2_pauses_precisely_when_external_model_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MEV_STRUCTURE_PROVIDER", "alphafold_db")
    session = {"mev_construct": {"sequence": "AVLG"}}

    with pytest.raises(ToolUnavailableError) as exc_info:
        await runner_additions.run_11_2(session, None, None)

    assert exc_info.value.tool_name == "AlphaFold/SwissModel"
    assert "no validated external structure" in exc_info.value.reason.lower()
    assert "sequence sketch" in exc_info.value.reason.lower()
    assert "structures" not in session


@pytest.mark.asyncio
async def test_step_11_2_accepts_exact_sequence_user_provided_pdb_without_public_coordinates() -> None:
    session = {
        "mev_construct": {"sequence": "AVLG"},
        "mev_structure_input": {
            "coordinateText": _coordinate_pdb(),
            "modelFormat": "pdb",
            "provider": "user-uploaded PDB",
            "method": "external model attachment",
            "source": "user-provided",
            "fileName": "mev-model.pdb",
            "contentType": "chemical/x-pdb",
        },
    }

    result = await runner_additions.run_11_2(session, None, None)

    assert result["source"] == "user-provided"
    assert result["provider"] == "user-uploaded PDB"
    assert result["provenance"]["status"] == "user-provided"
    assert result["provenance"]["method"] == "external model attachment"
    assert result["sequenceIdentityValidation"]["identityPercent"] == 100.0
    assert result["sequenceIdentityValidation"]["coveragePercent"] == 100.0
    assert result["coordinateDataAvailable"] is True
    assert "coordinateText" not in str(result)
    assert "coordinateText" not in str(session["structures"])
    assert session["validated_coordinate_data"][0]["coordinateText"].startswith("ATOM")


@pytest.mark.asyncio
async def test_step_11_2_rejects_coordinate_sequence_mismatch() -> None:
    session = {
        "mev_construct": {"sequence": "AVLA"},
        "mev_structure_input": {
            "coordinateText": _coordinate_pdb(),
            "provider": "user-uploaded PDB",
            "method": "external model attachment",
            "source": "user-provided",
        },
    }

    with pytest.raises(ToolUnavailableError) as exc_info:
        await runner_additions.run_11_2(session, None, None)

    assert "exact-sequence validation" in exc_info.value.reason
    assert "does not match" in exc_info.value.reason
    assert "structures" not in session


def test_structure_model_route_stores_the_runner_input_contract() -> None:
    from app.models import JobCreate, StructureModelAttachment
    from app.repo import repo
    from app.routes import attach_structure_model
    from app.tools.runner import clear_session, get_session

    job = repo.create(JobCreate(name="structure attachment regression", taxonId=1))
    try:
        response = attach_structure_model(
            job.id,
            StructureModelAttachment(
                modelText=_coordinate_pdb(),
                modelFormat="pdb",
                provider="user-uploaded PDB",
                method="external model attachment",
                fileName="mev-model.pdb",
            ),
        )
        assert response["status"] == "attached"
        attached = get_session(job.id)["mev_structure_input"]
        assert attached["coordinateText"].startswith("ATOM")
        assert attached["modelFormat"] == "pdb"
        assert attached["source"] == "user-provided"
        assert "external_structure_model" not in get_session(job.id)
    finally:
        clear_session(job.id)
        repo.delete(job.id)


@pytest.mark.asyncio
async def test_attached_route_payload_is_consumed_by_step_11_2() -> None:
    from app.models import JobCreate, StructureModelAttachment
    from app.repo import repo
    from app.routes import attach_structure_model
    from app.tools.runner import clear_session, get_session

    job = repo.create(JobCreate(name="structure route runner regression", taxonId=1))
    try:
        get_session(job.id)["mev_construct"] = {"sequence": "AVLG"}
        attach_structure_model(
            job.id,
            StructureModelAttachment(
                modelText=_coordinate_pdb(),
                modelFormat="pdb",
                provider="user-uploaded PDB",
                method="external model attachment",
                fileName="mev-model.pdb",
            ),
        )
        result = await runner_additions.run_11_2(get_session(job.id), job, None)
        assert result["coordinateDataAvailable"] is True
        assert result["sequenceIdentityValidation"]["identityPercent"] == 100.0
        assert get_session(job.id)["validated_coordinate_data"][0]["sequence"] == "AVLG"
    finally:
        clear_session(job.id)
        repo.delete(job.id)


@pytest.mark.asyncio
async def test_simulator_persists_safe_mev_structure_result_only() -> None:
    from app.models import JobCreate
    from app.repo import repo
    from app.simulator import engine
    from app.tools.runner import clear_session, get_session

    job = repo.create(JobCreate(name="structure persistence regression", taxonId=1))
    step = next(step for phase in job.phases for step in phase.steps if step.id == "11-2")
    get_session(job.id).update({
        "mev_construct": {"sequence": "AVLG"},
        "mev_structure_input": {
            "coordinateText": _coordinate_pdb(),
            "provider": "user-uploaded PDB",
            "method": "external model attachment",
            "source": "user-provided",
            "fileName": "mev-model.pdb",
        },
    })
    try:
        await engine._run_tool(job, 11, step)
        persisted = repo.get(job.id)
        assert persisted is not None
        persisted_step = next(s for phase in persisted.phases for s in phase.steps if s.id == "11-2")
        assert persisted_step.status == "success"
        assert persisted_step.result["provenance"]["status"] == "user-provided"
        assert "coordinateText" not in str(persisted_step.result)
        assert get_session(job.id)["validated_coordinate_data"][0]["coordinateText"].startswith("ATOM")
    finally:
        clear_session(job.id)
        repo.delete(job.id)


@pytest.mark.asyncio
async def test_step_11_2_hydrates_provider_pdb_url_for_downstream_coordinate_steps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A URL attachment is usable by 11-3 only after real PDB retrieval and validation."""
    full_pdb = "\n".join([
        "ATOM      1    N ALA A   1      -1.300   1.000   0.000  1.00 90.00           N",
        "ATOM      2   CA ALA A   1       0.000   0.000   0.000  1.00 90.00           C",
        "ATOM      3    C ALA A   1       1.300   1.000   0.000  1.00 90.00           C",
        "ATOM      4    N VAL A   2       2.500   1.000   0.000  1.00 90.00           N",
        "ATOM      5   CA VAL A   2       3.800   0.000   0.000  1.00 90.00           C",
        "ATOM      6    C VAL A   2       5.100   1.000   0.000  1.00 90.00           C",
        "ATOM      7    N LEU A   3       6.300   1.000   0.000  1.00 90.00           N",
        "ATOM      8   CA LEU A   3       7.600   0.000   0.000  1.00 90.00           C",
        "ATOM      9    C LEU A   3       8.900   1.000   0.000  1.00 90.00           C",
        "ATOM     10    N GLY A   4      10.100   1.000   0.000  1.00 90.00           N",
        "ATOM     11   CA GLY A   4      11.400   0.000   0.000  1.00 90.00           C",
        "ATOM     12    C GLY A   4      12.700   1.000   0.000  1.00 90.00           C",
        "END",
    ]) + "\n"
    monkeypatch.setattr(
        runner_additions,
        "_fetch_external_structure_coordinates",
        AsyncMock(return_value=full_pdb),
    )
    session = {
        "mev_construct": {"sequence": "AVLG"},
        "mev_structure_input": {
            "sequence": "AVLG",
            "modelUrl": "https://provider.example/mev.pdb",
            "provider": "real external provider",
            "method": "provider exact-sequence PDB model",
            "source": "real",
        },
    }

    result = await runner_additions.run_11_2(session, None, None)
    downstream = await runner_additions.run_11_3(session, None, None)

    assert result["coordinateDataAvailable"] is True
    assert result["provenance"]["modelUrl"] == "https://provider.example/mev.pdb"
    assert downstream["results"][0]["total_residues"] > 0
    assert session["validated_coordinate_data"][0]["coordinateText"] == full_pdb


@pytest.mark.asyncio
async def test_step_11_2_pauses_when_provider_url_cannot_supply_coordinates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        runner_additions,
        "_fetch_external_structure_coordinates",
        AsyncMock(side_effect=ValueError("provider returned an empty coordinate file")),
    )
    session = {
        "mev_construct": {"sequence": "AVLG"},
        "mev_structure_input": {
            "sequence": "AVLG",
            "modelUrl": "https://provider.example/mev.pdb",
            "provider": "real external provider",
            "method": "provider exact-sequence PDB model",
            "source": "real",
        },
    }

    with pytest.raises(ToolUnavailableError, match="did not return usable PDB coordinates"):
        await runner_additions.run_11_2(session, None, None)


def test_structure_requirements_exposes_exact_assembled_sequence_contract() -> None:
    from app.models import JobCreate
    from app.repo import repo
    from app.routes import structure_requirements

    job = repo.create(JobCreate(name="structure requirements regression", taxonId=1))
    try:
        assembly = next(step for phase in job.phases for step in phase.steps if step.id == "9-2")
        assembly.result = {"sequence": "AVLG"}
        repo.upsert(job)
        requirements = structure_requirements(job.id)
        assert requirements["step"] == "11-2"
        assert requirements["sequence"] == "AVLG"
        assert requirements["sequenceLength"] == 4
        assert len(requirements["sequenceFingerprint"]) == 64
        assert requirements["validation"]["coordinateAnalysisRequired"] is True
    finally:
        repo.delete(job.id)
