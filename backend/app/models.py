"""Pydantic models mirroring the TypeScript contracts in src/types/index.ts.

Field names and shapes MUST stay in sync with the frontend so the fetch
wrapper (`src/lib/api.ts`) and the Zustand store can consume them directly.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

JobStatus = Literal["created", "running", "paused", "completed", "failed"]
PhaseStatus = Literal["pending", "running", "completed", "failed", "paused", "skipped"]
StepStatus = Literal["pending", "running", "success", "failed", "skipped", "paused"]
EventType = Literal[
    "step_started",
    "step_progress",
    "step_completed",
    "step_failed",
    "filter_applied",
    "pipeline_paused",
    "pipeline_resumed",
    "pipeline_completed",
]


class StepError(BaseModel):
    message: str
    retries: int = 0
    firstFailedAt: Optional[str] = None
    lastFailedAt: Optional[str] = None
    severity: Literal["error", "pause", "warning"] = "error"
    tool: Optional[str] = None


class Epitope(BaseModel):
    id: str
    type: Literal["CTL", "HTL", "BCELL_LINEAR", "BCELL_CONFORMATIONAL"]
    sequence: str
    sourceProtein: str
    sourceProteinId: Optional[str] = None
    sourceProteinName: Optional[str] = None
    startPosition: Optional[int] = None
    hlaAllele: Optional[str] = None
    antigenicityScore: Optional[float] = None
    isAllergenic: Optional[bool] = None
    isToxic: Optional[bool] = None
    immunogenicityScore: Optional[float] = None
    ic50: Optional[float] = None
    percentileRank: Optional[float] = None
    windowLength: Optional[int] = None
    predictionMethod: Optional[str] = None
    source: Optional[Literal["real", "cached-real", "local-analysis", "unavailable", "user-provided"]] = None
    selected: bool = True


class Step(BaseModel):
    id: str
    phase: int
    number: int
    name: str
    tool: str
    status: StepStatus = "pending"
    percent: Optional[int] = None
    duration: Optional[int] = None
    startedAt: Optional[str] = None
    completedAt: Optional[str] = None
    error: Optional[StepError] = None
    result: Optional[dict] = None


class Phase(BaseModel):
    number: int
    name: str
    status: PhaseStatus = "pending"
    steps: list[Step] = Field(default_factory=list)
    duration: Optional[int] = None
    summary: Optional[str] = None
    filterLevel: Optional[dict] = None


class JobConfigModel(BaseModel):
    pathogenName: str
    strain: Optional[str] = None
    taxonId: Optional[int] = None
    source: Literal["pathogen", "fasta"] = "pathogen"
    fastaFileName: Optional[str] = None
    adjuvant: str = "ctxb"
    # Explicit opt-in for user-supplied signal peptide/full-adjuvant sequences.
    # Disabled preserves the legacy CTxB construct and ignores optional fields.
    enableMevEnhancements: bool = False
    adjuvantSequence: Optional[str] = None
    adjuvantSource: Optional[str] = None
    signalPeptideSequence: Optional[str] = None
    signalPeptideSource: Optional[str] = None
    cdHitThreshold: float = 0.8
    vaxijenThreshold: float = 0.5
    expressionHost: str = "ecoli"
    expressionVector: str = "pet28a"
    runImmuneSim: bool = True
    runDisulfide: bool = True
    enableCoverage: bool = True
    hlaMhc1: list[str] = Field(default_factory=list)
    hlaMhc2: list[str] = Field(default_factory=list)
    mhciPercentile: float = 2.0
    mhciiPercentile: float = 2.0
    bCellWindow: int = 16
    coverageRegions: list[str] = Field(default_factory=list)
    # When enabled, steps with a registered tool runner execute the real tool
    # (Phase 1 UniProt + CD-HIT are implemented). Unregistered steps simulate.
    realTools: bool = True
    reviewedOnly: bool = False


class Job(BaseModel):
    id: str
    name: str
    pathogenName: str
    strain: Optional[str] = None
    taxonId: Optional[int] = None
    status: JobStatus = "created"
    currentPhase: int = 1
    currentStep: int = 1
    progress: int = 0
    stepsCompleted: int = 0
    totalSteps: int = 0
    elapsedSeconds: int = 0
    estimatedRemaining: Optional[int] = None
    createdAt: str
    updatedAt: str
    config: JobConfigModel
    phases: list[Phase] = Field(default_factory=list)
    funnel: Optional[list[dict]] = None
    epitopes: list[Epitope] = Field(default_factory=list)


class MEVStructureInput(BaseModel):
    """External model attachment for the assembled MEV sequence.

    The runner, rather than the client, validates an attachment against the
    exact assembled MEV sequence. Therefore coordinate uploads may omit
    ``sequence``; URL-only submissions still need the submitted sequence and
    explicit 100% identity/coverage validation. Coordinate text is transient
    and is never part of a ``Job`` response.
    """

    sequence: Optional[str] = None
    provider: str = "user-provided"
    method: str = "external structure model"
    source: Literal["user-provided", "real"] = "user-provided"
    modelUrl: Optional[str] = None
    modelFormat: Optional[Literal["pdb"]] = None
    coordinateText: Optional[str] = Field(default=None, exclude=True)
    # Backward-compatible upload spelling used by the original API client.
    modelText: Optional[str] = Field(default=None, exclude=True)
    attachmentId: Optional[str] = None
    fileName: Optional[str] = None
    contentType: Optional[str] = None
    sequenceIdentity: Optional[float] = None
    sequenceCoverage: Optional[float] = None
    validationMethod: Optional[str] = None


# Compatibility name for callers using the original structure-upload contract.
StructureModelAttachment = MEVStructureInput


class JobCreate(BaseModel):
    name: str = ""
    pathogenName: str = "Streptococcus agalactiae"
    strain: Optional[str] = "GBS 2603V/R"
    taxonId: Optional[int] = None
    source: Literal["pathogen", "fasta"] = "pathogen"
    fastaFileName: Optional[str] = None
    # Transient upload payload. It is consumed into the run session and is
    # excluded from serialized job/config responses.
    fastaText: Optional[str] = Field(default=None, exclude=True)
    adjuvant: str = "ctxb"
    # Optional sequence extensions are never activated implicitly by payload data.
    enableMevEnhancements: bool = False
    adjuvantSequence: Optional[str] = None
    adjuvantSource: Optional[str] = None
    signalPeptideSequence: Optional[str] = None
    signalPeptideSource: Optional[str] = None
    expressionHost: str = "ecoli"
    expressionVector: str = "pet28a"
    hlaMhc1: list[str] = Field(default_factory=list)
    hlaMhc2: list[str] = Field(default_factory=list)
    mhciPercentile: float = 2.0
    mhciiPercentile: float = 2.0
    bCellWindow: int = 16
    cdHitThreshold: float = 0.8
    vaxijenThreshold: float = 0.5
    runImmuneSim: bool = True
    runDisulfide: bool = True
    enableCoverage: bool = True
    coverageRegions: list[str] = Field(default_factory=list)
    # Forwarded to JobConfigModel.realTools. Defaults on for jobs created via
    # POST /api/jobs; the seeded demo jobs set it off to stay offline-safe.
    realTools: bool = True
    reviewedOnly: bool = False


class PipelineEvent(BaseModel):
    id: str
    jobId: str
    type: EventType
    phase: Optional[int] = None
    step: Optional[int] = None
    tool: Optional[str] = None
    percent: Optional[int] = None
    message: Optional[str] = None
    duration: Optional[int] = None
    summary: Optional[dict] = None
    error: Optional[str] = None
    timestamp: str
