"use client";

import { useQuery } from "@tanstack/react-query";
import { fetchJobs } from "@/lib/api";
import type { Job } from "@/types";

/**
 * Fetch the real job list from GET /api/jobs. Polls while any job is active
 * (running/paused) so the dashboard and jobs list stay current.
 */
export function useJobs() {
  const query = useQuery<Job[]>({
    queryKey: ["jobs"],
    queryFn: fetchJobs,
    staleTime: 5_000,
    refetchInterval: (current) => {
      const jobs = current.state.data ?? [];
      const active = jobs.some((j) => j.status === "running" || j.status === "paused");
      return active ? 5_000 : false;
    },
  });

  return {
    jobs: query.data ?? [],
    isLoading: query.isLoading,
    isError: query.isError,
    refetch: query.refetch,
  };
}