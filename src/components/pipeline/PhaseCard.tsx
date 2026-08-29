"use client";

import { useState } from "react";
import Link from "next/link";
import { motion } from "framer-motion";
import { ChevronDown, ExternalLink, Loader2, CheckCircle2, XCircle, AlertTriangle, CircleDashed } from "lucide-react";
import { cn, formatDuration } from "@/lib/utils";
import { StepRow } from "@/components/pipeline/StepRow";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import type { Phase, Step } from "@/types";

function PhaseStatusIcon({ status }: { status: Phase["status"] }) {
  switch (status) {
    case "completed":
      return <CheckCircle2 className="h-5 w-5 shrink-0 text-emerald-500" />;
    case "running":
      return <Loader2 className="h-5 w-5 shrink-0 animate-spin text-blue-500" />;
    case "failed":
      return <XCircle className="h-5 w-5 shrink-0 text-red-500" />;
    case "paused":
      return <AlertTriangle className="h-5 w-5 shrink-0 text-amber-500" />;
    case "skipped":
      return <CircleDashed className="h-5 w-5 shrink-0 text-slate-400" />;
    default:
      return <CircleDashed className="h-5 w-5 shrink-0 text-slate-300" />;
  }
}

export function PhaseCard({
  phase,
  jobId,
  defaultOpen = false,
  onRetry,
  onSkip,
  onStop,
}: {
  phase: Phase;
  jobId: string;
  defaultOpen?: boolean;
  onRetry?: (step: Step) => void;
  onSkip?: (step: Step) => void;
  onStop?: () => void;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const completed = phase.steps.filter((s) => s.status === "success").length;
  const total = phase.steps.length;
  const Icon = phase.icon ?? CircleDashed;
  const hasResults = completed > 0;

  return (
    <motion.div
      layout
      className={cn(
        "overflow-hidden rounded-xl border bg-card transition-shadow",
        phase.status === "completed" && "border-emerald-200",
        phase.status === "running" && "border-blue-200 shadow-sm",
        phase.status === "failed" && "border-red-200",
        phase.status === "paused" && "border-amber-200",
        phase.status === "pending" && "border-border",
      )}
    >
      <Collapsible open={open} onOpenChange={setOpen}>
        <CollapsibleTrigger asChild>
          <button
            className={cn(
              "flex w-full items-center gap-3 px-4 py-3 text-left transition-colors hover:bg-muted/40",
            )}
          >
            <span
              className={cn(
                "flex h-9 w-9 shrink-0 items-center justify-center rounded-lg",
                phase.status === "completed" && "bg-emerald-50 text-emerald-600",
                phase.status === "running" && "bg-blue-50 text-blue-600",
                phase.status === "failed" && "bg-red-50 text-red-600",
                phase.status === "paused" && "bg-amber-50 text-amber-600",
                phase.status === "pending" && "bg-muted text-slate-400",
              )}
            >
              <Icon className="h-4.5 w-4.5" />
            </span>

            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2">
                <span className="text-xs font-semibold text-muted-foreground">
                  Phase {phase.number}
                </span>
                <h3 className="truncate text-sm font-semibold text-foreground">
                  {phase.name}
                </h3>
              </div>
              <div className="mt-0.5 flex items-center gap-2 text-[11px] text-muted-foreground">
                <span className="font-medium">
                  {completed}/{total} steps
                </span>
                {phase.duration != null && (
                  <>
                    <span className="text-slate-300">·</span>
                    <span>{formatDuration(phase.duration)}</span>
                  </>
                )}
                {phase.summary && (
                  <>
                    <span className="text-slate-300">·</span>
                    <span className="truncate text-emerald-600">{phase.summary}</span>
                  </>
                )}
              </div>
            </div>

            {phase.status === "running" && (
              <span className="mev-pulse-dot h-2.5 w-2.5 shrink-0 rounded-full bg-blue-500" />
            )}

            <PhaseStatusIcon status={phase.status} />

            {hasResults && jobId && (
              <Link
                href={`/jobs/${jobId}/phase/${phase.number}`}
                onClick={(e) => e.stopPropagation()}
                className="hidden items-center gap-1 rounded-md border border-border bg-background px-2 py-1 text-[11px] font-medium text-muted-foreground transition-colors hover:bg-muted hover:text-foreground sm:inline-flex"
              >
                Results
                <ExternalLink className="h-3 w-3" />
              </Link>
            )}

            <ChevronDown
              className={cn(
                "h-4 w-4 shrink-0 text-muted-foreground transition-transform duration-200",
                open && "rotate-180",
              )}
            />
          </button>
        </CollapsibleTrigger>

        <CollapsibleContent>
          <div className="border-t border-border bg-slate-50/50 px-3 py-2.5">
            <div className="flex flex-col gap-1">
              {phase.steps.map((step) => (
                <StepRow
                  key={step.id}
                  step={step}
                  onRetry={onRetry}
                  onSkip={onSkip}
                  onStop={onStop}
                />
              ))}
            </div>
          </div>
        </CollapsibleContent>
      </Collapsible>
    </motion.div>
  );
}