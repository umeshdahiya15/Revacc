import type { LucideIcon } from "lucide-react";

export type JobStatus = "created" | "running" | "paused" | "completed" | "failed";
export type PhaseStatus = "pending" | "running" | "completed" | "failed" | "paused" | "skipped";
export type StepStatus = "pending" | "running" | "success" | "failed" | "skipped" | "paused";
export type EpitopeType = "CTL" | "HTL" | "BCELL_LINEAR" | "BCELL_CONFORMATIONAL";
export type ApiKind = "api" | "scrape" | "local";

export interface StepDefinition {
  number: number;
  name: string;
  tool: string;
  /** stable id like "5-3" */
  id: string;
}

export interface PhaseDefinition {
  number: number;
  name: string;
  icon: LucideIcon;
  steps: StepDefinition[];
}

export interface StepError {
  message: string;
  retries: number;
  firstFailedAt?: string;
  lastFailedAt?: string;
  severity?: "error" | "pause" | "warning";
  tool?: string;
}

export interface Step {
  id: string;
  phase: number;
  number: number;
  name: string;
  tool: string;
  status: StepStatus;
  percent?: number;
  duration?: number;
  startedAt?: string;
  completedAt?: string;
  error?: StepError;
  result?: Record<string, unknown>;
}

export interface FilterFunnelLevel {
  key: string;
  label: string;
  count: number;
  filterLabel?: string;
  final?: boolean;
}

export interface Phase {
  number: number;
  name: string;
  /** Optional — server-serialized phases don't carry React components. */
  icon?: LucideIcon;
  status: PhaseStatus;
  steps: Step[];
  duration?: number;
  summary?: string;
  filterLevel?: FilterFunnelLevel;
}

export interface JobConfig {
  pathogenName: string;
  strain?: string;
  taxonId?: number | string;
  source: "pathogen" | "fasta";
  fastaFileName?: string;
  adjuvant: string;
  cdHitThreshold: number;
  vaxijenThreshold: number;
  expressionHost: string;
  expressionVector: string;
  runImmuneSim: boolean;
  runDisulfide: boolean;
  enableCoverage: boolean;
  hlaMhc1: string[];
  hlaMhc2: string[];
  mhciPercentile?: number;
  mhciiPercentile?: number;
  bCellWindow: number;
  coverageRegions: string[];
  realTools?: boolean;
  reviewedOnly?: boolean;
}

export type JobStatusMeta = {
  label: string;
  color: string;
  dot: string;
};

export interface Job {
  id: string;
  name: string;
  pathogenName: string;
  strain?: string;
  taxonId?: number | string;
  status: JobStatus;
  currentPhase: number;
  currentStep: number;
  progress: number; // 0-100
  stepsCompleted: number;
  totalSteps: number;
  elapsedSeconds: number;
  estimatedRemaining?: number;
  createdAt: string;
  updatedAt: string;
  config: JobConfig;
  phases: Phase[];
  epitopes?: Epitope[];
  funnel?: FilterFunnelLevel[];
}

export interface Epitope {
  id: string;
  type: EpitopeType;
  sequence: string;
  sourceProtein: string;
  sourceProteinId?: string;
  sourceProteinName?: string;
  startPosition?: number;
  hlaAllele?: string;
  hlaAlleles?: string[];
  antigenicityScore?: number;
  isAllergenic?: boolean;
  isToxic?: boolean;
  immunogenicityScore?: number;
  ic50?: number;
  percentileRank?: number;
  predictionMethod?: string;
  cytokineProfile?: {
    ifnGamma?: boolean | string;
    il4?: boolean | string;
    il10?: boolean | string;
    [key: string]: unknown;
  };
  windowLength?: number;
  selected: boolean;
}

export interface Protein {
  id: string;
  uniprotId: string;
  name: string;
  length: number;
  essential: boolean;
  localization: string;
  tmHelices: number;
  signalPeptide: boolean;
  antigenic: boolean;
  allergenic: boolean;
  virulent: boolean;
  humanHomolog: boolean;
  selected: boolean;
  molecularWeight: number;
  pI: number;
  instabilityIndex: number;
  gravy: number;
  aliphaticIndex: number;
}

export interface MEVProperty {
  property: string;
  value: string;
  status: "pass" | "warn" | "fail" | "info";
  note?: string;
}

export interface MEVConstruct {
  sequence: string;
  length: number;
  properties: MEVProperty[];
  linkerMap: LinkerSegment[];
}

export type SegmentType = "adjuvant" | "linker" | "ctl" | "htl" | "bcell" | "dna" | "other";

export interface LinkerSegment {
  type: SegmentType;
  label: string;
  sequence: string;
  start: number;
  end: number;
  epitopeId?: string;
}

export interface PopulationCoverage {
  global: number;
  regions: { region: string; coverage: number; populations: number }[];
}

export interface ImmuneSimPoint {
  day: number;
  igg?: number;
  igm?: number;
  iga?: number;
  ifnGamma?: number;
  il2?: number;
  il4?: number;
  il10?: number;
  memoryB?: number;
  ctl?: number;
  th?: number;
}

export interface PipelineEvent {
  id: string;
  jobId: string;
  type:
    | "step_started"
    | "step_progress"
    | "step_completed"
    | "step_failed"
    | "filter_applied"
    | "pipeline_paused"
    | "pipeline_resumed"
    | "pipeline_completed";
  phase?: number;
  step?: number;
  tool?: string;
  percent?: number;
  message?: string;
  duration?: number;
  summary?: Record<string, unknown>;
  error?: string;
  timestamp: string;
}

export interface PipelineError {
  phase: number;
  step: number;
  message: string;
  stepId?: string;
  retries?: number;
  tool?: string;
  severity?: "error" | "pause" | "warning";
}

export interface PhaseResultSummary {
  phase: number;
  title: string;
  cards: { label: string; value: string | number; hint?: string; tone?: "default" | "success" | "warning" | "error" | "info" }[];
  table?: {
    columns: string[];
    rows: Record<string, string | number | boolean | undefined>[];
  };
}

export interface ToolInfo {
  id: string;
  name: string;
  phase: number;
  usedFor: string;
  apiKind: ApiKind;
  endpoint?: string;
  availability: "online" | "scrape" | "local" | "rate_limited";
  notes?: string;
}

export interface ActivityEntry {
  id: string;
  jobId: string;
  jobName: string;
  message: string;
  timestamp: string;
  kind: "success" | "running" | "error" | "warning" | "info";
}
