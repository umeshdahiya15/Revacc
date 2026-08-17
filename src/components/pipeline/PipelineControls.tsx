"use client";

import { useState } from "react";
import {
  Braces,
  Download,
  FileArchive,
  FileCode2,
  FileSpreadsheet,
  FileText,
  FileJson,
  Pause,
  Play,
  ChevronDown,
  Loader2,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import type { ExportFormat } from "@/lib/report";
import type { Job } from "@/types";

export type { ExportFormat };

export function PipelineControls({
  job,
  onStart,
  onStop,
  onResume,
  onExport,
}: {
  job: Job;
  onStart?: () => void | Promise<void>;
  onStop?: () => void | Promise<void>;
  onResume?: () => void | Promise<void>;
  onExport?: (format: ExportFormat) => void;
}) {
  const [busy, setBusy] = useState<string | null>(null);
  const isRunning = job.status === "running";
  const isPaused = job.status === "paused";
  const isCompleted = job.status === "completed";
  const hasResults = job.stepsCompleted > 0;

  const run = async (action: string, fn?: () => void | Promise<void>) => {
    setBusy(action);
    try {
      await fn?.();
    } finally {
      setBusy(null);
    }
  };

  const ExportMenu = (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="outline" disabled={!hasResults}>
          <Download className="h-4 w-4" />
          Export
          <ChevronDown className="h-3.5 w-3.5 opacity-60" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" side="bottom" sideOffset={8} className="w-60">
        <DropdownMenuLabel>Reports</DropdownMenuLabel>
        <DropdownMenuItem onClick={() => onExport?.("report")}>
          <FileText className="h-4 w-4 text-red-500" /> Analytical report (PDF)
          <span className="ml-auto text-[10px] text-muted-foreground">charts · tables</span>
        </DropdownMenuItem>
        <DropdownMenuItem onClick={() => onExport?.("rawpdf")}>
          <Braces className="h-4 w-4 text-orange-500" /> Raw data — all steps (PDF)
        </DropdownMenuItem>
        <DropdownMenuItem onClick={() => onExport?.("rawjson")}>
          <FileJson className="h-4 w-4 text-amber-500" /> Raw data (JSON)
        </DropdownMenuItem>
        <DropdownMenuSeparator />
        <DropdownMenuLabel>Data files</DropdownMenuLabel>
        <DropdownMenuItem onClick={() => onExport?.("csv")}>
          <FileSpreadsheet className="h-4 w-4 text-emerald-500" /> Epitopes (CSV)
        </DropdownMenuItem>
        <DropdownMenuItem onClick={() => onExport?.("fasta")}>
          <FileCode2 className="h-4 w-4 text-sky-500" /> Construct (FASTA)
        </DropdownMenuItem>
        <DropdownMenuItem onClick={() => onExport?.("genbank")}>
          <FileCode2 className="h-4 w-4 text-indigo-500" /> Construct (GenBank)
        </DropdownMenuItem>
        <DropdownMenuSeparator />
        <DropdownMenuItem onClick={() => onExport?.("zip")}>
          <FileArchive className="h-4 w-4 text-blue-500" /> Full ZIP bundle
          <span className="ml-auto text-[10px] text-muted-foreground">everything</span>
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );

  return (
    <div className="flex flex-wrap items-center gap-2">
      {job.status === "created" && (
        <Button onClick={() => run("start", onStart)} disabled={busy !== null}>
          {busy === "start" ? <Loader2 className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />}
          Start Pipeline
        </Button>
      )}

      {isRunning && (
        <Button
          variant="secondary"
          onClick={() => run("stop", onStop)}
          disabled={busy !== null}
        >
          {busy === "stop" ? <Loader2 className="h-4 w-4 animate-spin" /> : <Pause className="h-4 w-4" />}
          Stop
        </Button>
      )}

      {isPaused && (
        <Button onClick={() => run("resume", onResume)} disabled={busy !== null}>
          {busy === "resume" ? <Loader2 className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />}
          Resume
        </Button>
      )}

      {isCompleted && (
        <Button onClick={() => onExport?.("report")} className="gap-1.5">
          <FileText className="h-4 w-4" />
          Generate Report
        </Button>
      )}

      {ExportMenu}
    </div>
  );
}
