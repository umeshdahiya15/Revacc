"""Graceful-pause fallback for external tools with no usable REST API.

Some tools in the canonical pipeline (PSORTb, DeepTMHMM, VaxiJen, AlgPred 2.0)
either have no public REST endpoint, are Cloudflare-blocked, or are DNS-dead.
Rather than fabricating results, the engine pauses the job with a clearly
labelled "external tool required: run manually" banner and lets the user
bypass the step.  Runners signal this by raising :class:`ToolUnavailableError`.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ToolUnavailableError(RuntimeError):
    """Raised when a pipeline tool cannot be reached and must be run manually.

    The simulator catches this and pauses the step instead of failing it,
    surfacing `tool_name`, `reason` and `workaround` to the frontend.
    """

    tool_name: str
    reason: str
    workaround: str
    # Optional data must already be reduced to a public-safe projection by
    # its caller. The engine adds it only as an additive step-result field.
    public_status: dict[str, object] | None = None

    def __str__(self) -> str:
        return f"{self.tool_name}: {self.reason} — {self.workaround}"


async def require_tool(tool_name: str, reason: str, workaround: str) -> None:
    """Always raise a :class:`ToolUnavailableError` for an unreachable tool.

    Exists so graceful-pause runners read as "try the tool, then pause":
    the body is intentionally empty — the raise below is unconditional.
    """
    raise ToolUnavailableError(tool_name=tool_name, reason=reason, workaround=workaround)


PSORTB_PAUSE: dict[str, str] = {
    "tool_name": "PSORTb",
    "reason": (
        "psortb.org does not resolve (DNS NXDOMAIN) and psort.org/psortb/ is "
        "Cloudflare-blocked (HTTP 403) — there is no usable REST API."
    ),
    "workaround": (
        "Run PSORTb v3.0.3 manually at https://www.psort.org/psortb/ and paste "
        "the subcellular-localization results, or bypass this step to treat all "
        "essential candidates as surface-exposed."
    ),
}

DEEPTMHMM_PAUSE: dict[str, str] = {
    "tool_name": "DeepTMHMM",
    "reason": (
        "DeepTMHMM is not exposed through the EBI Job Dispatcher REST API and the "
        "DTU BioLib service is a login-gated SPA with no anonymous endpoint."
    ),
    "workaround": (
        "Run DeepTMHMM manually at https://dtu.biolib.com/DeepTMHMM/ (or provide a "
        "BioLib token), or bypass to treat all candidates as non-transmembrane."
    ),
}

VAXIJEN_PAUSE: dict[str, str] = {
    "tool_name": "VaxiJen 2.0",
    "reason": (
        "VaxiJen 2.0 returns Cloudflare 403 to the submission endpoint "
        "(vaxijen2.0_post.php) — the form API is not scriptable."
    ),
    "workaround": (
        "Run VaxiJen 2.0 manually at https://www.ddg-pharmfac.net/vaxijen/ and paste "
        "the antigenicity scores, or bypass to treat all candidates as antigenic."
    ),
}

ALGPRED_PAUSE: dict[str, str] = {
    "tool_name": "AlgPred 2.0",
    "reason": (
        "AlgPred 2.0 has no REST endpoint; the web form (webs.iiitd.edu.in/raghava/"
        "algpred2/) is HTML-only and cannot be scripted reliably."
    ),
    "workaround": (
        "Run AlgPred 2.0 manually at https://webs.iiitd.edu.in/raghava/algpred2/ and "
        "paste the allergenicity results, or bypass to treat all candidates as "
        "non-allergenic."
    ),
}

SWISSMODEL_PAUSE: dict[str, str] = {
    "tool_name": "SWISS-MODEL",
    "reason": (
        "SWISS-MODEL requires token-based authentication for its /automodel API "
        "endpoint; no anonymous REST submission is available."
    ),
    "workaround": (
        "Create a free SWISS-MODEL account at https://swissmodel.expasy.org, "
        "obtain an API token from your account page, and run the structure "
        "modelling manually. Alternatively, bypass to skip structural modelling."
    ),
}

TOXINPRED_PAUSE: dict[str, str] = {
    "tool_name": "ToxinPred",
    "reason": (
        "ToxinPred web service (sites.ibbr-icr.org) is unreachable (connection "
        "failed during probe); no alternative REST API exists."
    ),
    "workaround": (
        "Run ToxinPred manually at https://sites.ibbr-icr.org/toxinpred/ and paste "
        "the toxicity results, or bypass to treat all sequences as non-toxic."
    ),
}

PROTEIN_SOL_PAUSE: dict[str, str] = {
    "tool_name": "Protein-Sol",
    "reason": (
        "Protein-Sol web service (protein-sol.org) is unreachable (connection "
        "failed during probe); no authenticated REST API is available."
    ),
    "workaround": (
        "Run Protein-Sol manually at https://protein-sol.org/ and paste the "
        "solubility results, or bypass to treat all sequences as soluble."
    ),
}

ERRAT_PAUSE: dict[str, str] = {
    "tool_name": "ERRAT",
    "reason": (
        "ERRAT web service (ermsoft.com) domain is for sale (HugeDomains) — "
        "the service is discontinued and there is no replacement REST API."
    ),
    "workaround": (
        "Run ERRAT manually via the Structure Analysis tool at "
        "https://swissmodel.expasy.org/assess (which provides ERRAT-like "
        "quality assessment), or bypass to skip structural quality evaluation."
    ),
}

PROSA_PAUSE: dict[str, str] = {
    "tool_name": "ProSA",
    "reason": (
        "ProSA web service (prosa.sbiligo.net) is unreachable (connection "
        "failed during probe); no authenticated REST API is available."
    ),
    "workaround": (
        "Run ProSA online at https://prosa.sbiligo.net/ manually and paste "
        "the Z-score, or bypass to skip structural quality evaluation."
    ),
}

# Pauses for tools we have now replaced with local computation.
# These configs are retained for traceability but are no longer triggered
# because the local runners handle these steps directly.
SOPMA_PAUSE: dict[str, str] = {
    "tool_name": "SOPMA",
    "reason": (
        "SOPMA secondary structure server is unreachable (HTTP timeout). "
        "Replaced with local Chou-Fasman implementation."
    ),
    "workaround": "Using local Chou-Fasman prediction (structure_local.py).",
}

IFN_EPITOP_PAUSE: dict[str, str] = {
    "tool_name": "IFNepitope",
    "reason": (
        "IFNepitope server is unreachable. Replaced with local motif "
        "scoring + MHC binding rules."
    ),
    "workaround": "Using local IFNepitope implementation (cytokine_local.py).",
}

IL4PRED_PAUSE: dict[str, str] = {
    "tool_name": "IL4Pred",
    "reason": "IL4Pred server is unreachable. Replaced with local Th2 motif scanning.",
    "workaround": "Using local IL4Pred implementation (cytokine_local.py).",
}

IL10PRED_PAUSE: dict[str, str] = {
    "tool_name": "IL10Pred",
    "reason": "IL10Pred server is unreachable. Replaced with local regulatory T-cell motif scanning.",
    "workaround": "Using local IL10Pred implementation (cytokine_local.py).",
}

ABCPRED_PAUSE: dict[str, str] = {
    "tool_name": "ABCpred",
    "reason": "ABCpred server is unreachable. Replaced with BepiPred local prediction.",
    "workaround": "Using local ABCpred implementation (bcell_local.py).",
}

ELLIPRO_PAUSE: dict[str, str] = {
    "tool_name": "Ellipro",
    "reason": "Ellipro server is unreachable. Replaced with local discontinuous B-cell epitope prediction.",
    "workaround": "Using local Ellipro implementation (bcell_local.py).",
}

CLONING_PAUSE: dict[str, str] = {
    "tool_name": "Cloning",
    "reason": "External cloning design tool unreachable. Replaced with local restriction/Gibson design.",
    "workaround": "Using local cloning implementation (adjuvant_dbd2_local.py).",
}

CIMMSIM_PAUSE: dict[str, str] = {
    "tool_name": "C-ImmSim",
    "reason": "C-ImmSim requires HPC submission. Replaced with local ODE immune simulation.",
    "workaround": "Using local C-ImmSim implementation (adjuvant_dbd2_local.py).",
}

ADJUVANT_PAUSE: dict[str, str] = {
    "tool_name": "Adjuvant Selection",
    "reason": "External adjuvant database unreachable. Replaced with local adjuvant selection.",
    "workaround": "Using local adjuvant selection (adjuvant_dbd2_local.py).",
}

DBD2_PAUSE: dict[str, str] = {
    "tool_name": "DbD2",
    "reason": "DbD2 primer design server unreachable. Replaced with local primer design.",
    "workaround": "Using local DbD2 implementation (adjuvant_dbd2_local.py).",
}

PROTEIN_PROPERTIES_PAUSE: dict[str, str] = {
    "tool_name": "Protein Properties",
    "reason": "External protein property predictor unreachable. Replaced with local computation.",
    "workaround": "Using local ProtParam implementation (mev_local.py).",
}
