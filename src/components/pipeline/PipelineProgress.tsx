"use client";

import { motion } from "framer-motion";
import { Clock, Layers } from "lucide-react";
import { PhaseCard } from "@/components/pipeline/PhaseCard";
import { Progress } from "@/components/ui/progress";
import { StatusBadge } from "@/components/common/StatusBadge";
import { cn, formatDuration, formatElapsedHMS } from "@/lib/utils";
import type { Job, Step } from "@/types";

export function PipelineProgress({
  job,
  onRetry,
  onSkip,
  onStop,
}: {
  job: Job;
  onRetry?: (step: Step) => void;
  onSkip?: (step: Step) => void;
  onStop?: () => void;
}) {
  const runningPhase = job.phases.find((p) => p.status === "running");
  const runningStep = runningPhase?.steps.find((s) => s.status === "running");

  return (
    <div className="space-y-4">
      {/* Overall progress summary */}
      <div className="rounded-xl border border-border bg-card p-4">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
              <h2 className="text-sm font-semibold text-foreground">Pipeline Progress</h2>
              <StatusBadge status={job.status} />
            </div>
            <p className="mt-1 text-xs text-muted-foreground">
              Phase {job.currentPhase} of {job.phases.length} · {job.stepsCompleted} of{" "}
              {job.totalSteps} steps completed
            </p>
            {runningStep && (
              <p className="mt-1.5 flex items-center gap-1.5 text-xs text-blue-600">
                <span className="mev-pulse-dot h-2 w-2 rounded-full bg-blue-500" />
                Now running: Phase {runningPhase?.number} · Step {runningPhase?.number}.{runningStep.number}{" "}
                — {runningStep.name} ({runningStep.tool})
              </p>
            )}
          </div>
          <div className="flex shrink-0 flex-col gap-1 text-right">
            <div className="flex items-center justify-end gap-1.5 text-xs text-muted-foreground lg:justify-start">
              <Clock className="h-3.5 w-3.5" />
              <span>Elapsed {formatElapsedHMS(job.elapsedSeconds)}</span>
            </div>
            <div className="flex items-center justify-end gap-1.5 text-xs text-muted-foreground lg:justify-start">
              <Layers className="h-3.5 w-3.5" />
              <span>
                Est. remaining {job.estimatedRemaining != null ? formatDuration(job.estimatedRemaining) : "—"}
              </span>
            </div>
          </div>
        </div>

        <div className="mt-3 flex items-center gap-3">
          <Progress
            value={job.progress}
            className="h-2.5"
            indicatorClassName={cn(
              job.status === "completed" && "bg-emerald-500",
              job.status === "failed" && "bg-red-500",
              job.status === "paused" && "bg-amber-500",
            )}
          />
          <span className="w-11 shrink-0 text-right text-sm font-semibold tabular-nums text-foreground">
            {job.progress}%
          </span>
        </div>
      </div>

      {/* Phase cards */}
      <div className="space-y-2.5">
        {job.phases.map((phase, i) => (
          <motion.div
            key={phase.number}
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: Math.min(i * 0.03, 0.4), duration: 0.25 }}
          >
            <PhaseCard
              phase={phase}
              jobId={job.id}
              defaultOpen={phase.status === "running" || phase.status === "failed" || phase.status === "paused"}
              onRetry={onRetry}
              onSkip={onSkip}
              onStop={onStop}
            />
          </motion.div>
        ))}
      </div>
    </div>
  );
}