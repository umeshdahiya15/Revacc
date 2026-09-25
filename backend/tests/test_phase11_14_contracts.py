from __future__ import annotations

import pytest

from app.tools import runner_additions


def _full_backbone_pdb() -> str:
    names = {"A": "ALA", "V": "VAL", "L": "LEU", "G": "GLY"}
    lines: list[str] = []
    serial = 1
    for residue_number, aa in enumerate("AVLG", 1):
        x = (residue_number - 1) * 3.8
        for atom, dx, dy, element in (
            ("N", -1.3, 1.0, "N"),
            ("CA", 0.0, 0.0, "C"),
            ("C", 1.3, 1.0, "C"),
        ):
            lines.append(
                f"ATOM  {serial:5d} {atom:>4} {names[aa]:>3} A{residue_number:4d}"
                f"    {x + dx:8.3f}{dy:8.3f}{0.0:8.3f}  1.00 90.00           {element:>2}"
            )
            serial += 1
    return "\n".join(lines + ["END"]) + "\n"


def _contact_pdb() -> str:
    residues = [("ALA", 0.0), ("VAL", 3.8), ("LEU", 7.6), ("GLY", 3.0)]
    return "\n".join(
        f"ATOM  {index:5d}  CA  {name:>3} A{index:4d}"
        f"    {x:8.3f}{0.0:8.3f}{0.0:8.3f}  1.00 90.00           C"
        for index, (name, x) in enumerate(residues, 1)
    ) + "\nEND\n"


@pytest.mark.asyncio
async def test_phase11_3_consumes_exact_transient_coordinates() -> None:
    session = {
        "validated_coordinate_data": [{
            "sequence": "AVLG",
            "coordinateText": _full_backbone_pdb(),
            "provider": "user-uploaded PDB",
        }],
    }

    result = await runner_additions.run_11_3(session, None, None)

    assert result["method"] == "ramachandran_local_coordinate_analysis"
    assert result["source"] == "local-analysis"
    assert result["provenance"]["syntheticValues"] is False
    assert result["results"][0]["total_residues"] > 0


@pytest.mark.asyncio
async def test_phase11_4_and_11_5_use_measured_contacts_and_provenance() -> None:
    session = {
        "validated_coordinate_data": [{
            "sequence": "AVLG",
            "coordinateText": _contact_pdb(),
            "provider": "user-uploaded PDB",
        }],
    }

    errat = await runner_additions.run_11_4(session, None, None)
    prosa = await runner_additions.run_11_5(session, None, None)

    assert errat["source"] == prosa["source"] == "local-analysis"
    assert errat["provenance"]["syntheticValues"] is False
    assert prosa["provenance"]["syntheticValues"] is False
    assert errat["results"][0]["total_contacts"] > 0
    assert prosa["results"][0]["total_pairs"] > 0


@pytest.mark.asyncio
async def test_phase12_and_phase13_local_outputs_declare_real_inputs() -> None:
    session = {
        "mev_construct": {"sequence": "ACDEFGHIKLMNPQRSTVWY"},
        "codon_optimized": {
            "codon_optimized": "GCTTGTGATGAAGATTTTGGTCATATTAATATGCCGCCGCAATCTACGTGTGG",
            "provenance": {"status": "real", "syntheticValues": False},
        },
    }

    disulfide = await runner_additions.run_13_1(session, None, None)
    restriction = await runner_additions.run_13_2(session, None, None)
    cloning = await runner_additions.run_13_3(session, None, None)

    assert disulfide["source"] == "local-analysis"
    assert disulfide["provenance"]["syntheticValues"] is False
    assert restriction["source"] == "local-analysis"
    assert restriction["provenance"]["syntheticValues"] is False
    assert cloning["source"] == "local-analysis"
    assert cloning["provenance"]["syntheticValues"] is False


@pytest.mark.asyncio
async def test_validated_model_drives_phases_11_to_14_without_fabricated_simulation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A real-coordinate attachment unlocks all wired downstream phases honestly."""
    from app.tools.graceful_pause import ToolUnavailableError

    class _JcatResponse:
        text = (
            'Improved DNA: <font class = "sequence">GCTGTTCTGGGT</font>'
            ' CAI-Value of the improved sequence: <font class = "sequence">0.80</font>'
            ' GC-Content of the improved sequence: <font class = "sequence">50.0</font>'
            '<font class = "sequence">50.0</font><font class = "sequence">50.0</font>'
        )

        def raise_for_status(self) -> None:
            return None

    class _JcatClient:
        def __init__(self, **_kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args) -> None:
            return None

        async def post(self, *_args, **_kwargs):
            return _JcatResponse()

    monkeypatch.setattr(runner_additions.httpx, "AsyncClient", _JcatClient)
    session = {
        "mev_construct": {"sequence": "AVLG"},
        "mev_structure_input": {
            "coordinateText": _full_backbone_contact_pdb(),
            "provider": "user-uploaded PDB",
            "method": "external structure model",
            "source": "user-provided",
        },
    }

    structure = await runner_additions.run_11_2(session, None, None)
    assert structure["provenance"]["syntheticValues"] is False
    for runner in (
        runner_additions.run_11_3,
        runner_additions.run_11_4,
        runner_additions.run_11_5,
        runner_additions.run_13_1,  # pipeline step 12-1
        runner_additions.run_12_1,  # pipeline step 13-1
        runner_additions.run_13_2,
        runner_additions.run_13_3,
    ):
        result = await runner(session, None, None)
        assert result.get("provenance", {}).get("syntheticValues") is False
        assert result.get("source") in {"local-analysis", "real"}

    assert session["codon_optimized"]["codon_optimized"] == "GCTGTTCTGGGT"
    # Without the external C-ImmSim service, phase 14-1 completes with the
    # local ODE immune model instead of pausing the pipeline.
    immune = await runner_additions.run_14_1(session, None, None)
    assert immune["provenance"]["status"] == "local-analysis"
    assert immune.get("peak_igG", 0) >= 0
    assert session.get("immune_simulation")


def _full_backbone_contact_pdb() -> str:
    names = {"A": "ALA", "V": "VAL", "L": "LEU", "G": "GLY"}
    atoms = []
    serial = 1
    for residue_number, aa in enumerate("AVLG", 1):
        x = (residue_number - 1) * 3.8
        if residue_number == 4:
            # Keep the peptide bond measurable while folding the fourth C-alpha
            # near residue 1, giving ERRAT/ProSA a measured non-local contact.
            coords = (("N", 8.0, 1.0, "N"), ("CA", 3.0, 0.0, "C"), ("C", 4.3, 1.0, "C"))
        else:
            coords = (("N", x - 1.3, 1.0, "N"), ("CA", x, 0.0, "C"), ("C", x + 1.3, 1.0, "C"))
        for atom, atom_x, atom_y, element in coords:
            atoms.append(
                f"ATOM  {serial:5d} {atom:>4} {names[aa]:>3} A{residue_number:4d}"
                f"    {atom_x:8.3f}{atom_y:8.3f}{0.0:8.3f}  1.00 90.00           {element:>2}"
            )
            serial += 1
    return "\n".join(atoms + ["END"]) + "\n"
