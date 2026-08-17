"use client";

import { useQuery } from "@tanstack/react-query";
import { fetchEpitopes } from "@/lib/api";
import type { Epitope } from "@/types";

export function useEpitopes(jobId?: string, enabled = true) {
  const query = useQuery<Epitope[]>({
    queryKey: ["epitopes", jobId],
    queryFn: () => fetchEpitopes(jobId!),
    enabled: Boolean(jobId) && enabled,
    refetchInterval: 1000 * 5,
    staleTime: 1000 * 4,
  });
  return {
    epitopes: query.data ?? [],
    isLoading: query.isLoading || (enabled && !query.isFetched),
    isError: query.isError,
    refetch: query.refetch,
  };
}