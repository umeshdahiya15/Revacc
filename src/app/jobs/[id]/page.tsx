"use client";

import { useEffect, useMemo, useRef } from "react";
import { useParams, useRouter } from "next/navigation";
import { useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Bug, Trash2, Wifi, WifiOff } from "lucide-react";
import { useJobStatus } from "@/hooks/useJobStatus";
import { usePipelineWebSocket } from "@/hooks/usePipelineWebSocket";
import { usePipelineStore } from "@/store/pipelineStore";
import { useToast } from "@/hooks/useToast";
import { pipelineControl, retryStep, skipStep, deleteJob, type PipelineAction } from "@/lib/api";
import { cn, timeAgo } from "@/lib/utils";
import { liveMetrics, type LiveMetric } from "@/lib/liveData";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { PipelineProgress } from "@/components/pipeline/PipelineProgress";
import { FilterFunnel } from "@/components/pipeline/FilterFunnel";
import { PipelineControls, type ExportFormat } from "@/components/pipeline/PipelineControls";
import { ErrorBanner } from "@/components/pipeline/ErrorBanner";
import { useExport } from "@/hooks/useExport";
import type { Job, PipelineError, Step } from "@/types";

export default function JobDetailPage() {
  const params = useParams<{ id: string }>();
  const jobId = params.id;
  const router = useRouter();
  const queryClient = useQueryClient();
  const { showToast } = useToast();
  const { job, isLoading } = useJobStatus(jobId);
  const { isConnected, lastEvent } = usePipelineWebSocket(jobId);

  const error = usePipelineStore((s) => s.error);
  const clearError = usePipelineStore((s) => s.clearError);
  const setActiveJob = usePipelineStore((s) => s.setActiveJob);
  const setJobs = usePipelineStore((s) => s.setJobs);
  const { exportJob } = useExport();

  const seededRef = useRef<string | undefined>(undefined);

  // Seed the store so WebSocket/poll mutations target this job.
  useEffect(() => {
    if (!jobId || !job || seededRef.current === jobId) return;
    const store = usePipelineStore.getState();
    if (store.activeJob?.id !== jobId) {
      seededRef.current = jobId;
      setActiveJob(job);
      setJobs([job, ...store.jobs.filter((j) => j.id !== jobId)]);
    }
  }, [jobId, job, setActiveJob, setJobs]);

  const funnel = useMemo(() => job?.funnel ?? [], [job]);
  const persistedError = useMemo<PipelineError | null>(() => {
    if (!job) return null;
    for (const phase of job.phases) {
      const step = phase.steps.find((candidate) => candidate.status === "failed" || candidate.status === "paused");
      if (step) {
        return {
          phase: phase.number,
          step: step.number,
          stepId: step.id,
          message: step.error?.message ?? "This step is paused or unavailable and needs review.",
          retries: step.error?.retries,
          tool: step.error?.tool ?? step.tool,
          severity: step.error?.severity ?? "pause",
        };
      }
    }
    return null;
  }, [job]);
  const displayError = error ?? persistedError;

  const onRetry = async (step: Step) => {
    if (!jobId) return;
    try {
      const updated = await retryStep(jobId, step.id);
      setActiveJob(updated);
      showToast("Step queued", "success", `${step.name} will be re-run by the engine.`);
    } catch (e) {
      showToast("Retry failed", "error", e instanceof Error ? e.message : "Request failed");
    }
  };

  const onSkip = async (step: Step) => {
    if (!jobId) return;
    try {
      const updated = await skipStep(jobId, step.id);
      clearError(step.id);
      setActiveJob(updated);
      showToast("Step skipped", "success", `${step.name} marked skipped — pipeline continues.`);
    } catch (e) {
      showToast("Skip failed", "error", e instanceof Error ? e.message : "Request failed");
    }
  };

  const onStop = async () => {
    if (!jobId) return;
    try {
      const updated = await pipelineControl(jobId, "pause");
      setActiveJob(updated);
      showToast("Pipeline stopped", "info", "Progress saved — resume to continue.");
    } catch (e) {
      showToast("Stop failed", "error", e instanceof Error ? e.message : "Request failed");
    }
  };

  /**
   * Lifecycle control against the real backend endpoints.
   */
  const control = async (action: PipelineAction) => {
    if (!jobId) return;
    try {
      const updated = await pipelineControl(jobId, action);
      setActiveJob(updated);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["job", jobId] }),
        queryClient.invalidateQueries({ queryKey: ["jobs"] }),
      ]);
    } catch (e) {
      showToast("Action failed", "error", e instanceof Error ? e.message : "Request failed");
    }
  };

  const onStart = () => control("start");
  const onResume = () => control("resume");

  const onExport = (format: ExportFormat) => {
    if (!job) return;
    exportJob(job, format);
    showToast("Exported", "success", `Pipeline results downloaded as .${format}`);
  };

  const onDelete = async () => {
    if (!jobId) return;
    try {
      await deleteJob(jobId);
      queryClient.invalidateQueries({ queryKey: ["jobs"] });
      showToast("Job deleted", "success", "The job and its data have been removed.");
      router.push("/jobs");
    } catch (e) {
      showToast("Delete failed", "error", e instanceof Error ? e.message : "Request failed");
    }
  };

  if (isLoading || !job) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-8 w-64" />
        <Skeleton className="h-40 w-full" />
        <Skeleton className="h-96 w-full" />
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <Button variant="ghost" size="sm" onClick={() => router.push("/jobs")} className="mb-1 -ml-2">
            <ArrowLeft className="h-4 w-4" />
            Jobs
          </Button>
          <h1 className="text-xl font-bold text-foreground">{job.name}</h1>
          <p className="mt-0.5 text-xs text-muted-foreground">
            <span className="font-medium text-slate-600">{job.pathogenName}</span>
            {job.strain ? ` · ${job.strain}` : ""}
            {job.taxonId ? ` · txid ${job.taxonId}` : ""} · created {timeAgo(job.createdAt)}
          </p>
        </div>

        <div className="flex flex-col items-end gap-2">
          <div className="flex items-center gap-2">
            <Badge
              variant={isConnected ? "default" : "outline"}
              className={cn("gap-1", isConnected && "bg-emerald-500/10 text-emerald-700")}
            >
              {isConnected ? (
                <Wifi className="h-3 w-3 text-emerald-500" />
              ) : (
                <WifiOff className="h-3 w-3 text-muted-foreground" />
              )}
              {isConnected ? "Live (WebSocket)" : "Polling"}
            </Badge>
          </div>
          <PipelineControls
            job={job}
            onStart={onStart}
            onStop={onStop}
            onResume={onResume}
            onExport={onExport}
          />
          <Button size="sm" variant="outline" onClick={onDelete} className="gap-1 text-red-600 hover:text-red-700">
            <Trash2 className="h-3.5 w-3.5" />
            Delete
          </Button>
        </div>
      </div>

      {/* Live event ticker */}
      {lastEvent && (
        <div className="flex items-center gap-2 rounded-lg border border-border bg-muted/30 px-3 py-2 text-[11px] text-muted-foreground">
          <Bug className="h-3.5 w-3.5 text-muted-foreground" />
          <span>Last event: </span>
          <code className="font-mono text-primary">{lastEvent.type}</code>
          {lastEvent.phase && (
            <span>
              · phase {lastEvent.phase}
              {lastEvent.step ? ` / step ${lastEvent.step}` : ""}
            </span>
          )}
          {lastEvent.tool && <span>· {lastEvent.tool}</span>}
          {lastEvent.percent != null && <span>· {lastEvent.percent}%</span>}
          <span className="ml-auto text-muted-foreground">{timeAgo(lastEvent.timestamp)}</span>
        </div>
      )}

      {/* Error banner */}
      {displayError && (
        <ErrorBanner
          error={{ message: displayError.message, retries: displayError.retries ?? 0, tool: displayError.tool, severity: displayError.severity }}
          stepLabel={`Phase ${displayError.phase} · Step ${displayError.step}`}
          onRetry={() => {
            const step = findStep(job, displayError.phase, displayError.step);
            if (step) onRetry(step);
          }}
          onSkip={() => {
            const step = findStep(job, displayError.phase, displayError.step);
            if (step) onSkip(step);
          }}
          onStop={onStop}
        />
      )}

      <PipelineProgress job={job} onRetry={onRetry} onSkip={onSkip} />

      <LiveMeasuredSummary job={job} />

      {funnel.length > 0 && <FilterFunnel levels={funnel} />}

      {job.status === "completed" && (
        <CompletedSummary job={job} onExport={onExport} />
      )}
    </div>
  );
}

function findStep(job: Job, phase: number, step: number): Step | undefined {
  const p = job.phases.find((ph) => ph.number === phase);
  return p?.steps.find((s) => s.number === step);
}

function CompletedSummary({ job, onExport }: { job: Job; onExport: (f: ExportFormat) => void }) {
  const router = useRouter();
  const lastPhase = job.phases[Math.max(0, job.phases.length - 1)];
  const successSteps = job.phases.reduce(
    (acc, p) => acc + p.steps.filter((s) => s.status === "success").length,
    0,
  );
  const skippedSteps = job.phases.reduce(
    (acc, p) => acc + p.steps.filter((s) => s.status === "skipped").length,
    0,
  );
  return (
    <div className="rounded-xl border border-emerald-200 bg-emerald-50/50 p-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-sm font-semibold text-foreground">Pipeline complete</h2>
          <p className="mt-1 text-xs text-muted-foreground">
            {successSteps} of {job.totalSteps} steps completed
            {skippedSteps > 0 ? ` · ${skippedSteps} skipped` : ""} across {job.phases.length} phases.
            Review the phase results to inspect every step.
          </p>
        </div>
        <div className="flex gap-2">
          <Button size="sm" variant="outline" onClick={() => onExport("fasta")}>
            Download FASTA
          </Button>
          <Button size="sm" variant="outline" onClick={() => onExport("rawjson")}>
            Raw JSON
          </Button>
          {lastPhase && (
            <Button size="sm" onClick={() => router.push(`/jobs/${job.id}/phase/${lastPhase.number}`)}>
              View final construct
            </Button>
          )}
        </div>
      </div>
    </div>
  );
}

function LiveMeasuredSummary({ job }: { job: Job }) {
  const metrics = liveMetrics(job);
  if (metrics.length === 0) return null;
  return (
    <section className="rounded-xl border border-border bg-card p-4" aria-label="Live measured pipeline values">
      <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
        <div>
          <h2 className="text-sm font-semibold text-foreground">Live measured results</h2>
          <p className="text-[11px] text-muted-foreground">
            Values are read from this Job&apos;s step results and epitope records. Missing or unavailable outputs are not shown as zero.
          </p>
        </div>
        <Badge variant="outline" className="text-[10px]">backend data</Badge>
      </div>
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-4">
        {metrics.map((metric) => <MeasuredMetric key={metric.key} metric={metric} />)}
      </div>
    </section>
  );
}

function MeasuredMetric({ metric }: { metric: LiveMetric }) {
  const unavailable = metric.value == null || metric.status === "unavailable";
  const formatted = unavailable
    ? "Unavailable"
    : typeof metric.value === "number"
      ? metric.key === "population-coverage"
        ? `${metric.value.toFixed(2)}%`
        : metric.key === "mev-length"
          ? `${metric.value.toLocaleString()} aa`
          : metric.value.toLocaleString()
      : metric.value;
  return (
    <div className={cn(
      "rounded-md border p-2",
      unavailable ? "border-amber-200 bg-amber-50/50" : "border-border bg-background",
    )}>
      <p className="truncate text-[11px] font-medium text-muted-foreground">{metric.label}</p>
      <p className={cn("mt-0.5 text-lg font-bold tabular-nums", unavailable ? "text-amber-700" : "text-foreground")}>
        {formatted}
      </p>
      <p className="truncate text-[10px] text-muted-foreground">
        {metric.status}{metric.method ? ` · ${metric.method}` : ""}
      </p>
      {unavailable && metric.reason && <p className="mt-0.5 line-clamp-2 text-[10px] text-amber-700">{metric.reason}</p>}
    </div>
  );
}
