"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { wsUrl, apiRequest } from "@/lib/api";
import { usePipelineStore } from "@/store/pipelineStore";
import type { Job, PipelineEvent } from "@/types";

interface WebSocketState {
  isConnected: boolean;
  isSimulating: boolean;
  lastEvent: PipelineEvent | null;
}

const POLL_INTERVAL = 5000;
const MAX_BACKOFF = 30000;
const BASE_BACKOFF = 1000;

function recomputeJob(job: Job): Job {
  let completed = 0;
  let total = 0;
  let currentPhase = 1;
  let currentStep = 1;

  for (const phase of job.phases) {
    for (const step of phase.steps) {
      total++;
      if (step.status === "success" || step.status === "skipped") completed++;
    }
  }

  const firstIncomplete = job.phases
    .flatMap((p) => p.steps.map((s) => ({ phase: p.number, step: s })))
    .find((s) => !["success", "skipped"].includes(s.step.status));

  if (firstIncomplete) {
    currentPhase = firstIncomplete.phase;
    currentStep = firstIncomplete.step.number;
  }

  return {
    ...job,
    currentPhase,
    currentStep,
    stepsCompleted: completed,
    totalSteps: total,
    progress: total === 0 ? 0 : Math.round((completed / total) * 100),
  };
}

/**
 * Real-time pipeline updates.
 *
 * 1. Attempts a native WebSocket connection to /ws/pipeline/{jobId}.
 * 2. On disconnection, reconnects with exponential backoff.
 * 3. While disconnected, polls GET /api/jobs/{id} every 5s.
 *
 * The backend is the only source of truth — there is no local simulation.
 */
export function usePipelineWebSocket(
  jobId: string | undefined,
) {
  const setConnected = usePipelineStore((s) => s.setConnected);
  const updateJob = usePipelineStore((s) => s.updateJob);
  const applyPipelineEvent = usePipelineStore((s) => s.applyPipelineEvent);

  const [state, setState] = useState<WebSocketState>({
    isConnected: false,
    isSimulating: false,
    lastEvent: null,
  });

  const retryRef = useRef(0);
  const socketRef = useRef<WebSocket | null>(null);

  const setLastEvent = useCallback((event: PipelineEvent) => {
    setState((prev) => ({ ...prev, lastEvent: event }));
  }, []);

  const handleMessage = useCallback(
    (raw: string) => {
      try {
        const event = JSON.parse(raw) as PipelineEvent;
        if (event.type) {
          applyPipelineEvent(event);
          setLastEvent(event);
        }
      } catch {
        /* ignore malformed frames */
      }
    },
    [applyPipelineEvent, setLastEvent],
  );

  const poll = useCallback(() => {
    if (!jobId) return;
    apiRequest<Job>(`/api/jobs/${jobId}`)
      .then((job) => {
        updateJob(jobId, () => recomputeJob(job));
      })
      .catch(() => {
        /* backend offline — retain last known state */
      });
  }, [jobId, updateJob]);

  /* ------------------------- WebSocket path ------------------------ */
  useEffect(() => {
    if (!jobId) return;

    let disposed = false;
    let ws: WebSocket | null = null;
    let pollTimer: number | undefined;
    let connectTimer: number | undefined;

    const schedulePoll = () => {
      pollTimer = window.setInterval(poll, POLL_INTERVAL);
    };

    const scheduleReconnect = () => {
      if (disposed) return;
      const delay = Math.min(BASE_BACKOFF * 2 ** retryRef.current, MAX_BACKOFF);
      retryRef.current += 1;
      connectTimer = window.setTimeout(connect, delay);
    };

    const connect = () => {
      if (disposed) return;
      try {
        ws = new WebSocket(wsUrl(jobId));
      } catch {
        scheduleReconnect();
        return;
      }
      socketRef.current = ws;

      ws.onopen = () => {
        if (disposed) return;
        retryRef.current = 0;
        setConnected(true);
        setState((prev) => ({ ...prev, isConnected: true }));
        if (pollTimer) window.clearInterval(pollTimer);
      };

      ws.onmessage = (event) => handleMessage(String(event.data));

      ws.onclose = () => {
        setConnected(false);
        setState((prev) => ({ ...prev, isConnected: false }));
        schedulePoll();
        scheduleReconnect();
      };

      ws.onerror = () => {
        ws?.close();
      };
    };

    connect();
    schedulePoll();

    return () => {
      disposed = true;
      ws?.close();
      if (pollTimer) window.clearInterval(pollTimer);
      if (connectTimer) window.clearTimeout(connectTimer);
    };
  }, [jobId, setConnected, handleMessage, poll]);

  /* ------------------------- Polling path ------------------------- */
  useEffect(() => {
    if (!jobId) return;
    const interval = window.setInterval(poll, POLL_INTERVAL);
    return () => window.clearInterval(interval);
  }, [jobId, poll]);

  return state;
}