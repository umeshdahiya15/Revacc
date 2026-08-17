"use client";

import { useParams, useRouter } from "next/navigation";
import { ArrowLeft, ArrowRight, ChevronLeft } from "lucide-react";
import { useJobStatus } from "@/hooks/useJobStatus";
import { useEpitopes } from "@/hooks/useEpitopes";
import { cn, formatDuration } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { StatusBadge } from "@/components/common/StatusBadge";
import { StepProgressIndicator } from "@/components/pipeline/StepProgressIndicator";
import { StepResultPanel } from "@/components/pipeline/StepResultPanel";
import { EpitopeTable } from "@/components/results/EpitopeTable";
import { Skeleton } from "@/components/ui/skeleton";
import type { Phase, Step } from "@/types";

const STEP_ICON_STYLE: Record<Step["status"], string> = {
  pending: "bg-muted text-slate-400",
  running: "bg-blue-50 text-blue-600",
  success: "bg-emerald-50 text-emerald-600",
  failed: "bg-red-50 text-red-600",
  skipped: "bg-slate-100 text-slate-500",
  paused: "bg-amber-50 text-amber-600",
};

export default function PhaseDetailPage() {
  const params = useParams<{ id: string; n: string }>();
  const router = useRouter();
  const jobId = params.id;
  const phaseNo = Number(params.n);
  const { job, isLoading } = useJobStatus(jobId);
  const { epitopes } = useEpitopes(jobId);

  if (isLoading || !job) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-8 w-64" />
        <Skeleton className="h-40 w-full" />
        <Skeleton className="h-96 w-full" />
      </div>
    );
  }

  const phase = job.phases.find((p) => p.number === phaseNo);
  if (!phase) {
    return (
      <div className="space-y-4">
        <Button variant="ghost" size="sm" onClick={() => router.push(`/jobs/${jobId}`)} className="-ml-2">
          <ChevronLeft className="h-4 w-4" /> Back to pipeline
        </Button>
        <div className="rounded-xl border border-border bg-card p-8 text-center text-sm text-muted-foreground">
          Phase {phaseNo} does not exist for this job.
        </div>
      </div>
    );
  }

  const nextPhase = phaseNo < job.phases.length ? phaseNo + 1 : undefined;

  return (
    <div className="space-y-6">
      {/* Header */}
      <div>
        <Button
          variant="ghost"
          size="sm"
          onClick={() => router.push(`/jobs/${jobId}`)}
          className="mb-1 -ml-2"
        >
          <ArrowLeft className="h-4 w-4" />
          Pipeline
        </Button>
        <div className="flex flex-wrap items-center gap-2">
          <Badge variant="outline" className="font-mono text-[10px]">Phase {phase.number}</Badge>
          <h1 className="text-xl font-bold text-foreground">{phase.name}</h1>
        </div>
        <p className="mt-1 text-xs text-muted-foreground">
          Job {jobId} · {phase.steps.length} step(s) ·{" "}
          {phase.steps.filter((s) => s.status === "success" || s.status === "skipped").length} completed
        </p>
      </div>

      {/* Phase status summary */}
      <div className="rounded-xl border border-border bg-card p-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-2">
            <Badge>Phase {phase.number}</Badge>
            <StatusBadge status={phase.status} />
          </div>
          {phase.duration != null && (
            <span className="text-xs text-muted-foreground">Duration {formatDuration(phase.duration)}</span>
          )}
        </div>
        <div className="mt-3 flex items-center gap-3">
          <StepProgressIndicator
            status={phase.status === "completed" ? "success" : phase.status === "failed" ? "failed" : phase.status}
            percent={phase.status === "completed" ? 100 : undefined}
          />
          <p className="text-xs text-muted-foreground">
            Real pipeline status — each step below reflects the live job record.
          </p>
        </div>
      </div>

      {/* Real epitope data (phases 5/6/7) */}
      {(phaseNo === 5 || phaseNo === 6 || phaseNo === 7) && (
        <EpitopeBlock phaseNo={phaseNo} epitopes={epitopes} />
      )}

      {/* Per-step results */}
      <PhaseSteps phase={phase} />

      {/* Phase-specific details from step results */}
      {phaseNo === 8 && <CoverageBlock phase={phase} />}
      {phaseNo === 9 && <ConstructBlock phase={phase} />}

      {/* Navigation */}
      <div className="flex items-center justify-between pt-2">
        <p className="text-xs text-muted-foreground">
          Phase {phase.number} of {job.phases.length}
          {nextPhase ? ` · next: ${job.phases[nextPhase - 1]?.name}` : ""}
        </p>
        {nextPhase && (
          <Button onClick={() => router.push(`/jobs/${jobId}/phase/${nextPhase}`)}>
            Next phase
            <ArrowRight className="h-4 w-4" />
          </Button>
        )}
      </div>
    </div>
  );
}

function PhaseSteps({ phase }: { phase: Phase }) {
  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-base">Step results</CardTitle>
        <p className="text-xs text-muted-foreground">
          Organized real output for every step — expand Raw data to inspect the full JSON result.
        </p>
      </CardHeader>
      <CardContent className="space-y-2">
        {phase.steps.map((step) => (
          <StepDetail key={step.id} step={step} />
        ))}
      </CardContent>
    </Card>
  );
}

function StepDetail({ step }: { step: Step }) {
  return (
    <div className={cn("rounded-lg border border-border p-3", step.status === "running" && "border-blue-200 bg-blue-50/30")}>
      <div className="flex flex-wrap items-center gap-2">
        <span className={cn("flex h-7 w-7 shrink-0 items-center justify-center rounded-lg", STEP_ICON_STYLE[step.status])}>
          <span className="text-[10px] font-semibold">{step.phase}.{step.number}</span>
        </span>
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-semibold text-foreground">{step.name}</p>
          <p className="text-[11px] text-muted-foreground">{step.tool}</p>
        </div>
        <div className="flex items-center gap-2">
          {step.status === "running" && step.percent != null && (
            <span className="text-xs font-semibold text-blue-600">{Math.round(step.percent)}%</span>
          )}
          {step.status === "success" && step.duration != null && (
            <span className="text-[11px] text-muted-foreground">{formatDuration(step.duration)}</span>
          )}
          <StatusBadge status={step.status} />
        </div>
      </div>
      <div className="mt-2">
        <StepResultPanel step={step} />
      </div>
    </div>
  );
}

function EpitopeBlock({ phaseNo, epitopes }: { phaseNo: number; epitopes: ReturnType<typeof useEpitopes>["epitopes"] }) {
  if (!epitopes || epitopes.length === 0) {
    return (
      <div className="rounded-xl border border-dashed border-border bg-card p-6 text-center text-xs text-muted-foreground">
        No epitope predictions recorded for this phase yet — they appear once the backend tools run.
      </div>
    );
  }
  if (phaseNo === 5) {
    return <EpitopeTable epitopes={epitopes.filter((e) => e.type === "CTL")} defaultType="CTL" filename="ctl-epitopes.csv" />;
  }
  if (phaseNo === 6) {
    return <EpitopeTable epitopes={epitopes.filter((e) => e.type === "HTL")} defaultType="HTL" filename="htl-epitopes.csv" />;
  }
  return (
    <EpitopeTable
      epitopes={epitopes.filter((e) => e.type.startsWith("BCELL"))}
      defaultType="BCELL_LINEAR"
      filename="bcell-epitopes.csv"
    />
  );
}

function CoverageBlock({ phase }: { phase: Phase }) {
  const result = phase.steps.find((s) => s.id === "8-1")?.result;
  if (!result || typeof result !== "object") return null;
  const data = result as Record<string, unknown>;
  const rows =
    data.coverage && Array.isArray(data.coverage)
      ? (data.coverage as { region?: string; coverage?: number }[])
      : [];
  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-base">Population coverage</CardTitle>
      </CardHeader>
      <CardContent>
        {rows.length === 0 ? (
          <p className="text-xs text-muted-foreground">No coverage rows available in step results.</p>
        ) : (
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
            {rows.map((r) => (
              <div key={r.region ?? r.coverage} className="rounded-md border border-border p-2">
                <p className="truncate text-[11px] font-medium text-muted-foreground">{r.region ?? "Region"}</p>
                <p className="text-lg font-bold tabular-nums text-foreground">
                  {r.coverage != null ? `${Number(r.coverage).toFixed(2)}%` : "—"}
                </p>
              </div>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function ConstructBlock({ phase }: { phase: Phase }) {
  const result = phase.steps.find((s) => s.id === "9-2")?.result;
  if (!result || typeof result !== "object") return null;
  const data = result as Record<string, unknown>;
  const sequence = typeof data.sequence === "string" ? data.sequence : null;
  const length = typeof data.length === "number" ? data.length : sequence?.length ?? null;
  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-base">MEV construct assembly</CardTitle>
      </CardHeader>
      <CardContent className="space-y-2">
        {length != null && (
          <p className="text-xs text-muted-foreground">Construct length: {length} aa</p>
        )}
        {sequence ? (
          <pre className="max-h-64 overflow-auto rounded-md bg-slate-950 p-3 font-mono text-[10px] leading-relaxed text-slate-200">
            {sequence.match(/.{1,60}/g)?.join("\n") ?? sequence}
          </pre>
        ) : (
          <p className="text-xs text-muted-foreground">No assembled sequence recorded yet.</p>
        )}
      </CardContent>
    </Card>
  );
}