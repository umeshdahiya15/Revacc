"use client";

import { useQuery } from "@tanstack/react-query";
import { fetchActivity } from "@/lib/api";
import type { ActivityEntry } from "@/types";

/**
 * Fetch the recent activity feed from GET /api/activity. Polls alongside the
 * job list so new pipeline events appear on the dashboard in near-real-time.
 */
export function useActivity(limit = 20) {
  const query = useQuery<ActivityEntry[]>({
    queryKey: ["activity", limit],
    queryFn: () => fetchActivity(limit),
    staleTime: 5_000,
    refetchInterval: 5_000,
  });

  return {
    entries: query.data ?? [],
    isLoading: query.isLoading,
    isError: query.isError,
  };
}