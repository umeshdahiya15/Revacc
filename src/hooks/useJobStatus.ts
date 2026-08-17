"use client";

import { useQuery } from "@tanstack/react-query";
import { apiRequest } from "@/lib/api";
import { usePipelineStore } from "@/store/pipelineStore";
import type { Job } from "@/types";

/**
 * Fetch a job from the live backend — GET /api/jobs/{id}.
 *
 * The store's `activeJob` overrides the query result so real-time WebSocket
 * mutations (via `usePipelineWebSocket`) win until the next refetch.
 */
export function useJobStatus(jobId: string | undefined) {
  const activeJob = usePipelineStore((s) => s.activeJob);

  const query = useQuery<Job | undefined>({
    queryKey: ["job", jobId],
    queryFn: async () => {
      if (!jobId) return undefined;
      return apiRequest<Job>(`/api/jobs/${jobId}`, { timeoutMs: 4000 });
    },
    enabled: !!jobId,
    retry: false,
    staleTime: 5_000,
    refetchInterval: (query) => {
      const current = query.state.data;
      if (current && (current.status === "running" || current.status === "paused")) {
        return 5_000;
      }
      return false;
    },
  });

  const job = activeJob?.id === jobId ? activeJob : query.data;

  return { job, isLoading: query.isLoading, isError: query.isError };
}