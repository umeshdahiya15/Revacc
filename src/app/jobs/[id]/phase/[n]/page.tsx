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

      {/* Phase-specific details from step results (all from the live job record) */}
      {phaseNo === 8 && <CoverageBlock phase={phase} />}
      {phaseNo === 9 && <ConstructBlock phase={phase} />}
      {phaseNo === 10 && <ValidationBlock phase={phase} />}
      {phaseNo === 11 && <StructureBlock phase={phase} />}
      {phaseNo === 14 && <ImmuneSimBlock phase={phase} />}

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

  // Backend (IEDB Population Coverage 3.0.2) returns an overall `coverage`
  // number plus a `by_area` map {region: percent}. Support both that and the
  // legacy array shape.
  let rows: { region: string; coverage: number }[] = [];
  if (data.by_area && typeof data.by_area === "object") {
    rows = Object.entries(data.by_area as Record<string, number>).map(([region, coverage]) => ({
      region,
      coverage: Number(coverage),
    }));
  } else if (Array.isArray(data.coverage)) {
    rows = (data.coverage as { region?: string; coverage?: number }[]).map((r) => ({
      region: r.region ?? "Region",
      coverage: Number(r.coverage ?? 0),
    }));
  }
  const overall = typeof data.coverage === "number" ? (data.coverage as number) : undefined;
  const method = typeof data.method === "string" ? (data.method as string) : undefined;

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-base">Population coverage</CardTitle>
        {method && <p className="text-[11px] text-muted-foreground">{method}</p>}
      </CardHeader>
      <CardContent className="space-y-3">
        {overall != null && (
          <div className="rounded-md border border-primary/30 bg-primary/5 p-2">
            <p className="text-[11px] font-medium text-muted-foreground">World coverage</p>
            <p className="text-2xl font-bold tabular-nums text-foreground">{overall.toFixed(2)}%</p>
          </div>
        )}
        {rows.length === 0 ? (
          <p className="text-xs text-muted-foreground">No coverage regions available in step results.</p>
        ) : (
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
            {rows.map((r) => (
              <div key={r.region} className="rounded-md border border-border p-2">
                <p className="truncate text-[11px] font-medium text-muted-foreground">{r.region}</p>
                <p className="text-lg font-bold tabular-nums text-foreground">{r.coverage.toFixed(2)}%</p>
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


/** Read a step's result object by id. */
function stepResult(phase: Phase, id: string): Record<string, unknown> | null {
  const r = phase.steps.find((s) => s.id === id)?.result;
  return r && typeof r === "object" ? (r as Record<string, unknown>) : null;
}

function num(v: unknown): number | null {
  return typeof v === "number" && !Number.isNaN(v) ? v : null;
}

function Metric({
  label,
  value,
  suffix,
  ok,
}: {
  label: string;
  value: string;
  suffix?: string;
  ok?: boolean;
}) {
  return (
    <div className="rounded-md border border-border p-2">
      <p className="truncate text-[11px] font-medium text-muted-foreground">{label}</p>
      <p
        className={cn(
          "text-lg font-bold tabular-nums",
          ok === true ? "text-emerald-600" : ok === false ? "text-red-600" : "text-foreground",
        )}
      >
        {value}
        {suffix ? <span className="ml-0.5 text-xs font-normal text-muted-foreground">{suffix}</span> : null}
      </p>
    </div>
  );
}

/** Phase 10 — MEV construct validation (physicochemical + safety/antigenicity). */
function ValidationBlock({ phase }: { phase: Phase }) {
  const pp = (stepResult(phase, "10-1")?.physicochemical as Record<string, unknown>) ?? {};
  const antigen = stepResult(phase, "10-2");
  const allergen = stepResult(phase, "10-3");
  const toxic = stepResult(phase, "10-4");
  const soluble = stepResult(phase, "10-5");

  const mw = num(pp.molecular_weight);
  const pI = num(pp.isoelectric_point);
  const instab = num(pp.instability_index);
  const gravy = num(pp.gravy);
  const aliph = num(pp.aliphatic_index);

  const antigenScore = num(antigen?.score);
  const allergenScore = num(allergen?.score);
  const toxScore = num(toxic?.score);
  const solScore = num(soluble?.score);

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-base">MEV construct validation</CardTitle>
        <p className="text-[11px] text-muted-foreground">Physicochemical, antigenicity, allergenicity, toxicity and solubility from the live step results.</p>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-5">
          {mw != null && <Metric label="Molecular weight" value={mw.toLocaleString()} suffix="Da" />}
          {pI != null && <Metric label="Isoelectric point (pI)" value={pI.toFixed(2)} />}
          {instab != null && (
            <Metric label="Instability index" value={instab.toFixed(1)} ok={instab < 40} />
          )}
          {gravy != null && <Metric label="GRAVY" value={gravy.toFixed(3)} />}
          {aliph != null && <Metric label="Aliphatic index" value={aliph.toFixed(1)} />}
        </div>
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
          {antigenScore != null && (
            <Metric label="Antigenicity (VaxiJen)" value={antigenScore.toFixed(3)} ok={antigenScore >= 0.5} />
          )}
          {allergenScore != null && (
            <Metric
              label="Allergenicity (AlgPred)"
              value={allergenScore.toFixed(3)}
              ok={Boolean(allergen && allergen.is_allergen === false)}
            />
          )}
          {toxScore != null && (
            <Metric label="Toxicity (ToxinPred)" value={toxScore.toFixed(3)} ok={Boolean(toxic && toxic.is_toxic === false)} />
          )}
          {solScore != null && (
            <Metric label="Solubility (Protein-Sol)" value={solScore.toFixed(3)} ok={solScore >= 0.5} />
          )}
        </div>
      </CardContent>
    </Card>
  );
}

/** Phase 11 — MEV 3D structure validation (Ramachandran / ERRAT / ProSA). */
function StructureBlock({ phase }: { phase: Phase }) {
  const rama = stepResult(phase, "11-3");
  const errat = stepResult(phase, "11-4");
  const prosa = stepResult(phase, "11-5");

  const favored = num(rama?.favored_percent);
  const allowed = num(rama?.allowed_percent);
  const outliers = num(rama?.outlier_percent);
  const residues = num(rama?.total_residues);
  const erratScore = num(errat?.errat_score);
  const prosaZ = num(prosa?.prosa_zscore);

  if (favored == null && erratScore == null && prosaZ == null) return null;

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-base">MEV 3D structure validation</CardTitle>
      </CardHeader>
      <CardContent className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-6">
        {favored != null && <Metric label="Ramachandran favored" value={favored.toFixed(1)} suffix="%" ok={favored >= 90} />}
        {allowed != null && <Metric label="Allowed" value={allowed.toFixed(1)} suffix="%" />}
        {outliers != null && <Metric label="Outliers" value={outliers.toFixed(1)} suffix="%" ok={outliers < 5} />}
        {residues != null && <Metric label="Residues" value={String(residues)} />}
        {erratScore != null && <Metric label="ERRAT quality" value={erratScore.toFixed(1)} ok={erratScore >= 80} />}
        {prosaZ != null && <Metric label="ProSA Z-score" value={prosaZ.toFixed(2)} />}
      </CardContent>
    </Card>
  );
}

/** Phase 14 — immune simulation (C-ImmSim) summary. */
function ImmuneSimBlock({ phase }: { phase: Phase }) {
  const sim = stepResult(phase, "14-1");
  if (!sim) return null;
  const peakIgg = num(sim.peak_igG);
  const sero = num(sim.seroconversion_day);
  const th1 = num(sim.peak_Th1);
  const th2 = num(sim.peak_Th2);
  if (peakIgg == null && th1 == null) return null;

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-base">Immune simulation (C-ImmSim)</CardTitle>
      </CardHeader>
      <CardContent className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        {peakIgg != null && <Metric label="Peak IgG" value={peakIgg.toLocaleString(undefined, { maximumFractionDigits: 0 })} />}
        {sero != null && <Metric label="Seroconversion" value={`Day ${sero}`} />}
        {th1 != null && <Metric label="Peak Th1" value={th1.toFixed(1)} />}
        {th2 != null && <Metric label="Peak Th2" value={th2.toFixed(1)} />}
      </CardContent>
    </Card>
  );
}
