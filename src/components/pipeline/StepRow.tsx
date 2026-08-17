"use client";

import { useState } from "react";
import { ChevronDown } from "lucide-react";
import { cn, formatDuration } from "@/lib/utils";
import { StepProgressIndicator } from "@/components/pipeline/StepProgressIndicator";
import { ErrorBanner } from "@/components/pipeline/ErrorBanner";
import { StepResultPanel } from "@/components/pipeline/StepResultPanel";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import type { Step } from "@/types";

export function StepRow({
  step,
  onRetry,
  onSkip,
  onStop,
  showError = true,
  showTool = true,
}: {
  step: Step;
  onRetry?: (step: Step) => void;
  onSkip?: (step: Step) => void;
  onStop?: () => void;
  showError?: boolean;
  showTool?: boolean;
}) {
  const [showRaw, setShowRaw] = useState(false);
  const failed = step.status === "failed";
  const paused = step.status === "paused";
  const hasContent = step.result !== undefined || step.error !== undefined;

  return (
    <div className="group flex flex-col gap-1">
      <div
        className={cn(
          "flex items-center gap-2.5 rounded-md px-2 py-1.5 transition-colors",
          step.status === "running" && "bg-blue-50",
          (failed || paused) && "bg-red-50/60",
        )}
      >
        <StepProgressIndicator status={step.status} percent={step.percent} size="sm" />

        <span className="w-7 shrink-0 text-[11px] font-medium text-muted-foreground">
          {step.phase}.{step.number}
        </span>

        <TooltipProvider delayDuration={200}>
          <Tooltip>
            <TooltipTrigger asChild>
              <span className="min-w-0 flex-1 truncate text-[13px] text-foreground">
                {step.name}
              </span>
            </TooltipTrigger>
            <TooltipContent>
              <p>{step.name}</p>
              <p className="text-xs text-muted-foreground">Step {step.phase}.{step.number}</p>
            </TooltipContent>
          </Tooltip>
        </TooltipProvider>

        {showTool && (
          <span className="hidden shrink-0 text-[11px] text-muted-foreground sm:block">
            {step.tool}
          </span>
        )}

        {step.status === "running" && (
          <span className="shrink-0 text-[11px] font-medium text-blue-600">
            {step.percent != null ? `${Math.round(step.percent)}%` : "…"}
          </span>
        )}

        {step.status === "success" && (
          <span className="shrink-0 text-[11px] text-muted-foreground">
            {formatDuration(step.duration)}
          </span>
        )}

        {failed && (
          <span className="shrink-0 text-[11px] font-medium text-red-600">Failed</span>
        )}
        {paused && (
          <span className="shrink-0 text-[11px] font-medium text-amber-600">Paused</span>
        )}
        {step.status === "skipped" && (
          <span className="shrink-0 text-[11px] font-medium text-slate-500">Skipped</span>
        )}

        {hasContent && (
          <button
            onClick={(e) => {
              e.preventDefault();
              e.stopPropagation();
              setShowRaw((v) => !v);
            }}
            className="flex shrink-0 items-center gap-0.5 rounded px-1 text-[10px] font-medium text-muted-foreground transition-colors hover:bg-border hover:text-foreground"
          >
            {showRaw ? "Hide" : "Raw"}
            <ChevronDown className={cn("h-3 w-3 transition-transform", showRaw && "rotate-180")} />
          </button>
        )}
      </div>

      {failed && showError && step.error && (
        <ErrorBanner
          error={step.error}
          stepLabel={`Step ${step.phase}.${step.number} · ${step.name}`}
          onRetry={() => onRetry?.(step)}
          onSkip={() => onSkip?.(step)}
          onStop={onStop}
        />
      )}

      {showRaw && hasContent && <StepResultPanel step={step} />}
    </div>
  );
}