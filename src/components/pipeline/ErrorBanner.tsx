"use client";

import { motion } from "framer-motion";
import { AlertTriangle, RotateCcw, SkipForward, StopCircle } from "lucide-react";
import { Button } from "@/components/ui/button";
import type { StepError } from "@/types";

export function ErrorBanner({
  error,
  stepLabel,
  onRetry,
  onSkip,
  onStop,
}: {
  error: StepError;
  stepLabel: string;
  onRetry: () => void;
  onSkip: () => void;
  onStop?: () => void;
}) {
  return (
    <motion.div
      initial={{ opacity: 0, y: -8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.25, ease: "easeOut" }}
      className={`my-1.5 overflow-hidden rounded-lg border ${
        error.severity === "warning"
          ? "border-amber-200 bg-amber-50"
          : error.severity === "pause"
            ? "border-orange-200 bg-orange-50"
            : "border-red-200 bg-red-50"
      }`}
    >
      <div className="flex items-start gap-2.5 p-3">
        <div className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-full ${
          error.severity === "warning"
            ? "bg-amber-100"
            : error.severity === "pause"
              ? "bg-orange-100"
              : "bg-red-100"
        }`}>
          <AlertTriangle className={`h-4 w-4 ${
            error.severity === "warning"
              ? "text-amber-600"
              : error.severity === "pause"
                ? "text-orange-600"
                : "text-red-600"
          }`} />
        </div>
        <div className="min-w-0 flex-1">
          <p className="text-xs font-semibold text-red-800">{stepLabel}</p>
          <p className="mt-0.5 text-xs text-red-700">
            {error.message}
          </p>
          <p className="mt-1 text-[11px] text-red-600">
            Retry attempts: {error.retries}/3 exhausted
            {error.lastFailedAt && <> · Last failed: {new Date(error.lastFailedAt).toLocaleTimeString()}</>}
            {error.tool && <> · Tool: {error.tool}</>}
          </p>
        </div>
        <div className="flex shrink-0 flex-wrap justify-end gap-1.5">
          <Button size="sm" variant="outline" onClick={onRetry} className="gap-1 bg-white">
            <RotateCcw className="h-3 w-3" />
            Retry Step
          </Button>
          <Button size="sm" variant="outline" onClick={onSkip} className="gap-1 bg-white">
            <SkipForward className="h-3 w-3" />
            Skip Step
          </Button>
          {onStop && (
            <Button size="sm" variant="outline" onClick={onStop} className="gap-1 bg-white text-red-600 hover:text-red-700">
              <StopCircle className="h-3 w-3" />
              Stop Pipeline
            </Button>
          )}
        </div>
      </div>
    </motion.div>
  );
}