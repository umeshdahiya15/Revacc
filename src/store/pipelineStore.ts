import { create } from "zustand";
import type {
  Job,
  PipelineError,
  PipelineEvent,
  Step,
  StepStatus,
} from "@/types";

export interface PipelineStoreState {
  jobs: Job[];
  activeJob: Job | null;
  isConnected: boolean;
  error: PipelineError | null;
  events: PipelineEvent[];

  setJobs: (jobs: Job[]) => void;
  setActiveJob: (job: Job | null) => void;
  setConnected: (connected: boolean) => void;
  updateJob: (jobId: string, updater: (job: Job) => Job) => void;
  updateStepStatus: (stepId: string, status: StepStatus, meta?: Partial<Step> & { result?: Record<string, unknown> }) => void;
  applyPipelineEvent: (event: PipelineEvent) => void;
  setError: (error: PipelineError) => void;
  clearError: (stepId?: string) => void;
  pushEvents: (events: PipelineEvent[]) => void;
  reset: () => void;
}

function mutateJobSteps(
  job: Job,
  mutate: (step: Step) => Step,
): Job {
  return {
    ...job,
    phases: job.phases.map((phase) => ({
      ...phase,
      steps: phase.steps.map(mutate),
    })),
  };
}

export const usePipelineStore = create<PipelineStoreState>((set, get) => ({
  jobs: [],
  activeJob: null,
  isConnected: false,
  error: null,
  events: [],

  setJobs: (jobs) => set({ jobs }),
  setActiveJob: (job) => set({ activeJob: job }),
  setConnected: (isConnected) => set({ isConnected }),

  updateJob: (jobId, updater) => {
    const jobs = get().jobs.map((j) => (j.id === jobId ? updater(j) : j));
    set({ jobs });
    const { activeJob } = get();
    if (activeJob?.id === jobId) {
      set({ activeJob: jobs.find((j) => j.id === jobId) ?? null });
    }
  },

  updateStepStatus: (stepId, status, meta = {}) => {
    const { activeJob } = get();
    if (!activeJob) return;

    const updated = mutateJobSteps(activeJob, (step) => {
      if (step.id !== stepId) return step;
      const next: Step = { ...step, ...meta, status };
      if (status === "running") {
        next.startedAt = next.startedAt ?? new Date().toISOString();
      }
      if (status === "success") {
        next.completedAt = new Date().toISOString();
        if (step.startedAt) {
          next.duration = Math.max(
            1,
            Math.round((Date.now() - new Date(step.startedAt).getTime()) / 1000),
          );
        }
      }
      return next;
    });
    set({ activeJob: updated });
    get().updateJob(activeJob.id, () => updated);
  },

  applyPipelineEvent: (event) => {
    const { activeJob } = get();
    if (!activeJob) return;

    const phase = activeJob.phases.find((p) => p.number === event.phase);
    if (!phase) return;
    const stepNum = event.step ?? 1;

    let updated = activeJob;
    if (event.type === "step_started") {
      const stepId = `${event.phase}-${stepNum}`;
      updated = mutateJobSteps(activeJob, (step) => {
        if (step.id !== stepId) return step;
        return {
          ...step,
          status: "running",
          startedAt: step.startedAt ?? new Date().toISOString(),
          percent: 0,
        };
      });
    } else if (event.type === "step_progress") {
      const stepId = `${event.phase}-${stepNum}`;
      updated = mutateJobSteps(activeJob, (step) => {
        if (step.id !== stepId || step.status === "success") return step;
        return {
          ...step,
          status: "running",
          percent: Math.min(100, Math.max(5, event.percent ?? step.percent ?? 10)),
        };
      });
    } else if (event.type === "step_completed") {
      const stepId = `${event.phase}-${stepNum}`;
      updated = mutateJobSteps(activeJob, (step) => {
        if (step.id !== stepId) return step;
        return {
          ...step,
          status: "success",
          percent: 100,
          duration: event.duration ?? step.duration,
          completedAt: new Date().toISOString(),
        };
      });
    } else if (event.type === "step_failed") {
      const stepId = `${event.phase}-${stepNum}`;
      updated = mutateJobSteps(activeJob, (step) => {
        if (step.id !== stepId) return step;
        return {
          ...step,
          status: "failed",
          error: {
            message: event.error ?? "Unknown step failure",
            retries: 3,
            lastFailedAt: new Date().toISOString(),
            tool: event.tool,
            severity: "error",
          },
        };
      });
      set({
        error: {
          phase: event.phase ?? 0,
          step: stepNum,
          message: event.error ?? "Unknown step failure",
          stepId: `${event.phase}-${stepNum}`,
          tool: event.tool,
          severity: "error",
        },
      });
    } else if (event.type === "filter_applied") {
      if (event.summary && typeof event.summary === "object") {
        const funnel = (event.summary as { funnel?: PipelineEvent["summary"] }).funnel;
        if (Array.isArray(funnel) && funnel.length > 0) {
          updated = {
            ...updated,
            funnel: funnel as Job["funnel"],
          };
        }
      }
    } else if (event.type === "pipeline_paused") {
      updated = { ...updated, status: "paused" };
    } else if (event.type === "pipeline_resumed") {
      updated = { ...updated, status: "running" };
    } else if (event.type === "pipeline_completed") {
      updated = { ...updated, status: "completed", progress: 100, estimatedRemaining: 0 };
    }

    set({ activeJob: updated, events: [...get().events.slice(-199), event] });
    get().updateJob(activeJob.id, () => updated);
  },

  setError: (error) => set({ error }),
  clearError: (stepId) => {
    if (stepId === undefined || get().error?.stepId === stepId) {
      set({ error: null });
    }
  },
  pushEvents: (events) => set({ events: [...get().events, ...events].slice(-200) }),
  reset: () =>
    set({ activeJob: null, error: null, events: [], isConnected: false }),
}));