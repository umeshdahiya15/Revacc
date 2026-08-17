/**
 * Lightweight fetch wrapper for the Revacc FastAPI backend.
 */

import type { ActivityEntry, Epitope, Job } from "@/types";

export class ApiError extends Error {
  status: number;
  constructor(message: string, status = 500) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

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
    const response = await fetch(`${API_BASE_URL}${path}`, {
      method,
      headers: {
        "Content-Type": "application/json",
        Accept: "application/json",
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
  const host = API_BASE_URL.replace(/^https?:\/\//, "");
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${protocol}//${host}/ws/pipeline/${jobId}`;
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