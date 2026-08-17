"use client";

import { cn } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import {
  CheckCircle2,
  CircleDashed,
  PauseCircle,
  Play,
  XCircle,
  AlertTriangle,
} from "lucide-react";
import type { JobStatus, PhaseStatus, StepStatus } from "@/types";

type AnyStatus = JobStatus | PhaseStatus | StepStatus;

const STYLES: Record<AnyStatus, { label: string; className: string; icon: "ok" | "err" | "warn" | "run" | "idle" | "pause" }> = {
  created: { label: "Created", className: "bg-slate-100 text-slate-600", icon: "idle" },
  running: { label: "Running", className: "bg-blue-100 text-blue-700", icon: "run" },
  paused: { label: "Paused", className: "bg-amber-100 text-amber-700", icon: "pause" },
  completed: { label: "Completed", className: "bg-emerald-100 text-emerald-700", icon: "ok" },
  success: { label: "Success", className: "bg-emerald-100 text-emerald-700", icon: "ok" },
  failed: { label: "Failed", className: "bg-red-100 text-red-700", icon: "err" },
  pending: { label: "Pending", className: "bg-slate-100 text-slate-500", icon: "idle" },
  skipped: { label: "Skipped", className: "bg-slate-100 text-slate-500", icon: "idle" },
};

const ICONS = {
  ok: CheckCircle2,
  err: XCircle,
  warn: AlertTriangle,
  run: Play,
  idle: CircleDashed,
  pause: PauseCircle,
};

export function StatusBadge({
  status,
  className,
  withIcon = true,
}: {
  status: AnyStatus;
  className?: string;
  withIcon?: boolean;
}) {
  const style = STYLES[status] ?? STYLES.pending;
  const Icon = ICONS[style.icon];
  return (
    <Badge
      variant="outline"
      className={cn(
        "inline-flex items-center gap-1 border-transparent font-medium",
        style.className,
        className,
      )}
    >
      {withIcon && <Icon className="h-3 w-3" />}
      {style.label}
    </Badge>
  );
}