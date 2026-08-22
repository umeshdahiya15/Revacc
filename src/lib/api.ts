/**
 * Lightweight fetch wrapper for the Revacc FastAPI backend.
 *
 * URL resolution (in priority order):
 *   1. NEXT_PUBLIC_API_URL env var (set by the frontend deployment)
 *   2. User-configured URL in Settings (localStorage)
 *   3. Railway backend fallback (including SSR and missing-env deployments)
 */

import type { ActivityEntry, Epitope, Job } from "@/types";

export const DEFAULT_API_URL = "https://revacc-production.up.railway.app";

export class ApiError extends Error {
  status: number;
  constructor(message: string, status = 500) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

/**
 * Return a safe absolute HTTP(S) API origin.
 *
 * A hostname entered without a scheme is common in deployment settings, but
 * fetch treats it as a path when interpolated into a request URL. Deployed
 * hosts default to HTTPS; explicit local hostnames retain the convenient HTTP
 * development default.
 */
export function normalizeApiBaseUrl(url: string): string {
  const value = url.trim();
  if (!value || value.startsWith("/")) return DEFAULT_API_URL;

  const hasHttpScheme = /^https?:\/\//i.test(value);
  const hasUnsupportedScheme = /^[a-z][a-z\d+.-]*:\/\//i.test(value) && !hasHttpScheme;
  if (hasUnsupportedScheme) return DEFAULT_API_URL;

  let candidate = value;
  if (!hasHttpScheme) {
    try {
      const hostname = new URL(`http://${value}`).hostname.toLowerCase();
      const isLocalhost = hostname === "localhost"
        || hostname === "[::1]"
        || hostname === "0.0.0.0"
        || /^127(?:\.\d{1,3}){3}$/.test(hostname);
      candidate = `${isLocalhost ? "http" : "https"}://${value}`;
    } catch {
      return DEFAULT_API_URL;
    }
  }

  try {
    const parsed = new URL(candidate);
    if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
      return DEFAULT_API_URL;
    }
    return parsed.href.replace(/\/+$/, "");
  } catch {
    return DEFAULT_API_URL;
  }
}

function resolveApiBase(): string {
  // 1. Explicit env var (highest priority)
  const envUrl = process.env.NEXT_PUBLIC_API_URL;
  if (envUrl?.trim()) return normalizeApiBaseUrl(envUrl);

  // 2. User-configured URL in Settings (localStorage)
  if (typeof window !== "undefined") {
    try {
      const saved = JSON.parse(localStorage.getItem("revacc:settings") ?? "{}");
      if (typeof saved.apiUrl === "string" && saved.apiUrl.trim()) {
        return normalizeApiBaseUrl(saved.apiUrl);
      }
    } catch { /* ignore */ }
  }

  // Keep deployed and SSR requests pointed at the configured backend even
  // when NEXT_PUBLIC_API_URL is missing from the frontend environment.
  return DEFAULT_API_URL;
}

function normalizeBaseUrl(url: string): string {
  return normalizeApiBaseUrl(url);
}

export interface RequestOptions {
  method?: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
  body?: unknown;
  headers?: Record<string, string>;
  timeoutMs?: number;
}

export async function apiRequest<T>(
  path: string,
  options: RequestOptions = {},
): Promise<T> {
  const { method = "GET", body, headers = {}, timeoutMs = 30000 } = options;

  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), timeoutMs);

  try {
    const baseUrl = normalizeBaseUrl(resolveApiBase());
    const response = await fetch(`${baseUrl}${path}`, {
      method,
      headers: {
        "Content-Type": "application/json",
        Accept: "application/json",
        ...(baseUrl.includes("ngrok") ? { "ngrok-skip-browser-warning": "true" } : {}),
        ...headers,
      },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: controller.signal,
    });

    if (!response.ok) {
      let message = `Request failed (${response.status})`;
      try {
        const data = (await response.json()) as { detail?: string; message?: string };
        message = data.detail ?? data.message ?? message;
      } catch {
        /* ignore parse errors */
      }
      throw new ApiError(message, response.status);
    }

    return (await response.json()) as T;
  } catch (error) {
    if (error instanceof ApiError) throw error;
    if (error instanceof DOMException && error.name === "AbortError") {
      throw new ApiError(`Request timed out after ${timeoutMs}ms`, 408);
    }
    throw new ApiError(
      error instanceof Error ? error.message : "Network error",
      0,
    );
  } finally {
    clearTimeout(timeout);
  }
}

export function wsUrl(jobId: string): string {
  const base = normalizeBaseUrl(resolveApiBase());
  const wsBase = base.replace(/^http/, "ws");
  return `${wsBase}/ws/pipeline/${jobId}`;
}

export type PipelineAction = "start" | "pause" | "resume" | "stop";

/**
 * POST /api/jobs/{id}/{action} — backend lifecycle control.
 */
export async function pipelineControl(jobId: string, action: PipelineAction): Promise<Job> {
  return apiRequest<Job>(`/api/jobs/${jobId}/${action}`, { method: "POST", timeoutMs: 5000 });
}

/**
 * POST /api/jobs/{id}/steps/{stepId}/retry — reset a failed/paused step so
 * the engine re-runs its real tool, and resume the pipeline.
 */
export async function retryStep(jobId: string, stepId: string): Promise<Job> {
  return apiRequest<Job>(`/api/jobs/${jobId}/steps/${stepId}/retry`, {
    method: "POST",
    timeoutMs: 5000,
  });
}

/**
 * POST /api/jobs/{id}/steps/{stepId}/skip — mark a step skipped and continue.
 */
export async function skipStep(jobId: string, stepId: string): Promise<Job> {
  return apiRequest<Job>(`/api/jobs/${jobId}/steps/${stepId}/skip`, {
    method: "POST",
    timeoutMs: 5000,
  });
}

/**
 * GET /api/jobs — full job list from the backend.
 */
export async function fetchJobs(): Promise<Job[]> {
  return apiRequest<Job[]>("/api/jobs");
}

/**
 * GET /api/jobs/{id}/epitopes — real predicted epitopes for a job.
 */
export async function fetchEpitopes(jobId: string): Promise<Epitope[]> {
  return apiRequest<Epitope[]>(`/api/jobs/${jobId}/epitopes`, { timeoutMs: 4000 });
}

export const COMPARISON_METRICS = [
  ["mev_length", "MEV length (aa)"],
  ["ctl_epitopes", "CTL epitopes"],
  ["htl_epitopes", "HTL epitopes"],
  ["bcell_epitopes", "B-cell epitopes"],
  ["population_coverage", "Population coverage (%)"],
] as const;

export interface ComparisonFunnelLevel {
  key: string;
  label: string;
  count: number | null;
  filterLabel?: string;
  final?: boolean;
}

export interface ComparisonProvenance {
  status: "real" | "cached-real" | "local-analysis" | "user-provided" | "unavailable" | "paused" | "partial" | "error" | string;
  method?: string | null;
  reason?: string | null;
  [key: string]: unknown;
}

export interface ComparisonJob {
  id: string;
  name: string;
  status: Job["status"];
  funnel: ComparisonFunnelLevel[];
  mevMetrics: Record<string, number | string | null>;
  provenance: ComparisonProvenance;
}

export interface JobsComparison {
  funnelStages: Array<{ key: string; label: string }>;
  jobs: ComparisonJob[];
}

/** Align the union of real funnel stages while preserving backend order. */
export function comparisonStages(jobs: ComparisonJob[]): Array<{ key: string; label: string }> {
  const stages = new Map<string, { key: string; label: string }>();
  jobs.forEach((job) => job.funnel.forEach((level) => {
    if (!stages.has(level.key)) stages.set(level.key, { key: level.key, label: level.label });
  }));
  return Array.from(stages.values());
}

export function compareJobs(jobIds: string[]): Promise<JobsComparison> {
  const ids = jobIds.map(encodeURIComponent).join(",");
  return apiRequest<JobsComparison>(`/api/jobs/compare?ids=${ids}`);
}


/**
 * GET /api/activity — recent pipeline events across all jobs.
 */
export async function fetchActivity(limit = 20): Promise<ActivityEntry[]> {
  return apiRequest<ActivityEntry[]>(`/api/activity?limit=${limit}`);
}

/**
 * DELETE /api/jobs/{id} — remove a job and its data.
 */
export async function deleteJob(jobId: string): Promise<void> {
  await apiRequest(`/api/jobs/${jobId}`, { method: "DELETE", timeoutMs: 5000 });
}

/**
 * GET /api/jobs/{id}/events — event history for a job.
 */
export async function fetchJobEvents(
  jobId: string,
  sinceId?: string,
): Promise<import("@/types").PipelineEvent[]> {
  const qs = sinceId ? `?since_id=${sinceId}` : "";
  return apiRequest<import("@/types").PipelineEvent[]>(
    `/api/jobs/${jobId}/events${qs}`,
    { timeoutMs: 5000 },
  );
}