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
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { PipelineProgress } from "@/components/pipeline/PipelineProgress";
import { FilterFunnel } from "@/components/pipeline/FilterFunnel";
import { PipelineControls, type ExportFormat } from "@/components/pipeline/PipelineControls";
import { ErrorBanner } from "@/components/pipeline/ErrorBanner";
import { useExport } from "@/hooks/useExport";
import type { Job, Step } from "@/types";

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
      {error && (
        <ErrorBanner
          error={{ message: error.message, retries: error.retries ?? 0, tool: error.tool, severity: error.severity }}
          stepLabel={`Phase ${error.phase} · Step ${error.step}`}
          onRetry={() => {
            const step = findStep(job, error.phase, error.step);
            if (step) onRetry(step);
          }}
          onSkip={() => {
            const step = findStep(job, error.phase, error.step);
            if (step) onSkip(step);
          }}
          onStop={onStop}
        />
      )}

      <PipelineProgress job={job} onRetry={onRetry} onSkip={onSkip} />

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