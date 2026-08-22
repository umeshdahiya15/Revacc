import type { Job, Provenance, ProvenanceStatus, Step } from "@/types";

export function asRecord(value: unknown): Record<string, unknown> | null {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

export function resultProvenance(result: unknown): Provenance | null {
  const record = asRecord(result);
  const provenance = asRecord(record?.provenance);
  if (provenance) return provenance as Provenance;
  return null;
}

export function stepResult(job: Job, id: string): Record<string, unknown> | null {
  for (const phase of job.phases) {
    const step = phase.steps.find((candidate) => candidate.id === id);
    const result = asRecord(step?.result);
    if (result) return result;
  }
  return null;
}

export function isUnavailableStep(step: Step | undefined): boolean {
  if (!step) return false;
  const provenance = resultProvenance(step.result);
  const status = provenance?.status;
  const result = asRecord(step.result);
  return (
    step.status === "paused" ||
    step.status === "failed" ||
    result?._paused === true ||
    status === "unavailable" ||
    status === "paused" ||
    status === "partial" ||
    status === "error"
  );
}

export function stepProvenance(step: Step | undefined): Provenance | null {
  if (!step) return null;
  return step.provenance ?? resultProvenance(step.result);
}

export function provenanceLabel(provenance: Provenance | null): string {
  if (!provenance) return "provenance not supplied";
  return [
    provenance.status,
    provenance.method,
    provenance.source,
    provenance.database,
    provenance.release ? `release ${provenance.release}` : undefined,
  ].filter(Boolean).join(" · ");
}

export interface LiveMetric {
  key: string;
  label: string;
  value: number | string | null;
  status: ProvenanceStatus;
  method?: string;
  reason?: string;
}

function numberFrom(result: Record<string, unknown> | null, keys: string[]): number | null {
  if (!result) return null;
  for (const key of keys) {
    const value = result[key];
    if (typeof value === "number" && Number.isFinite(value)) return value;
  }
  return null;
}

function stepById(job: Job, id: string): Step | undefined {
  return job.phases.flatMap((phase) => phase.steps).find((step) => step.id === id);
}

function metricFromStep(job: Job, key: string, label: string, stepId: string, resultKeys: string[]): LiveMetric | null {
  const step = stepById(job, stepId);
  const result = asRecord(step?.result);
  const provenance = stepProvenance(step);
  const value = numberFrom(result, resultKeys);
  if (value == null && !isUnavailableStep(step)) return null;
  return {
    key,
    label,
    value: isUnavailableStep(step) ? null : value,
    status: isUnavailableStep(step) ? "unavailable" : (provenance?.status ?? "unknown"),
    method: provenance?.method ?? (typeof result?.method === "string" ? result.method : undefined),
    reason: provenance?.reason ?? (typeof result?.reason === "string" ? result.reason : undefined),
  };
}

export function liveMetrics(job: Job): LiveMetric[] {
  const metrics: LiveMetric[] = [];
  const specs: [string, string, string, string[]][] = [
    ["reference-proteome", "Reference proteome", "1-1", ["proteins", "total", "count"]],
    ["essential", "Essential proteins", "2-1", ["essential", "essential_count", "count"]],
    ["surface-exposed", "Surface-exposed", "2-2", ["surface_exposed_count", "count"]],
    ["virulence", "Virulence factors", "3-3", ["virulence_count", "count"]],
    ["human-homology", "Non-human-homolog candidates", "3-4", ["non_homologous_count", "count"]],
    ["population-coverage", "Population coverage", "8-1", ["coverage"]],
    ["mev-length", "MEV length", "9-2", ["mev_length", "length"]],
  ];
  for (const [key, label, stepId, resultKeys] of specs) {
    const metric = metricFromStep(job, key, label, stepId, resultKeys);
    if (metric) metrics.push(metric);
  }

  const epitopes = job.epitopes ?? [];
  const epitopeCounts: [string, string, string, number][] = [
    ["ctl-epitopes", "CTL epitopes", "5-1", epitopes.filter((epitope) => epitope.type === "CTL").length],
    ["htl-epitopes", "HTL epitopes", "6-1", epitopes.filter((epitope) => epitope.type === "HTL").length],
    ["bcell-epitopes", "B-cell epitopes", "7-1", epitopes.filter((epitope) => epitope.type.startsWith("BCELL")).length],
  ];
  for (const [key, label, stepId, value] of epitopeCounts) {
    const step = stepById(job, stepId);
    const result = asRecord(step?.result);
    const provenance = stepProvenance(step);
    const resultValue = numberFrom(result, ["count", "epitopes", "predicted", "selected"]);
    if (value > 0) {
      const sourceStatuses = new Set(
        epitopes
          .filter((epitope) => key === "ctl-epitopes" ? epitope.type === "CTL" : key === "htl-epitopes" ? epitope.type === "HTL" : epitope.type.startsWith("BCELL"))
          .map((epitope) => epitope.source ?? epitope.provenance?.status ?? "unknown"),
      );
      metrics.push({ key, label, value, status: sourceStatuses.size === 1 ? Array.from(sourceStatuses)[0] : "mixed" });
    } else if (isUnavailableStep(step)) {
      metrics.push({ key, label, value: null, status: "unavailable", method: provenance?.method, reason: provenance?.reason });
    } else if (resultValue != null) {
      metrics.push({ key, label, value: resultValue, status: provenance?.status ?? "unknown", method: provenance?.method });
    }
  }
  return metrics;
}
