"use client";

import { useState } from "react";
import Link from "next/link";
import { Plus } from "lucide-react";
import { useJobs } from "@/hooks/useJobs";
import { compareJobs, comparisonStages as alignComparisonStages, COMPARISON_METRICS, pipelineControl } from "@/lib/api";
import { useQueryClient } from "@tanstack/react-query";
import { formatDuration, timeAgo } from "@/lib/utils";
import { useToast } from "@/hooks/useToast";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import { StatusBadge } from "@/components/common/StatusBadge";
import type { ComparisonJob } from "@/lib/api";
import type { Job } from "@/types";

const phaseNames = (job: Job) => job.phases.map((p) => p.name);

export default function JobsPage() {
  const { jobs, isLoading } = useJobs();
  const [selected, setSelected] = useState<string[]>([]);
  const [comparison, setComparison] = useState<Awaited<ReturnType<typeof compareJobs>> | null>(null);
  const [comparisonLoading, setComparisonLoading] = useState(false);
  const toggleSelected = (id: string) => setSelected((current) => current.includes(id) ? current.filter((item) => item !== id) : [...current, id]);
  const loadComparison = async () => {
    if (selected.length < 2) return;
    setComparisonLoading(true);
    try {
      setComparison(await compareJobs(selected));
    } catch {
      setComparison(null);
    } finally {
      setComparisonLoading(false);
    }
  };
  const comparisonStages = comparison?.funnelStages.length
    ? comparison.funnelStages
    : comparison ? alignComparisonStages(comparison.jobs) : [];

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold text-foreground">Pipeline Jobs</h1>
          <p className="text-sm text-muted-foreground">
            {isLoading ? "Loading…" : `${jobs.length} jobs · 14-phase reverse-vaccinology pipeline`}
          </p>
        </div>
        <Button asChild>
          <Link href="/jobs/new">
            <Plus className="h-4 w-4" />
            New Job
          </Link>
        </Button>
      </div>

      <div className="flex flex-wrap items-center gap-2 rounded-xl border border-border bg-card p-3">
        <span className="text-xs text-muted-foreground">Compare completed runs:</span>
        <Button size="sm" variant="outline" disabled={selected.length < 2 || comparisonLoading} onClick={loadComparison}>
          {comparisonLoading ? "Comparing…" : `Compare ${selected.length || ""}`}
        </Button>
        {comparison && (
          <div className="basis-full space-y-4 overflow-x-auto pt-2">
            <div>
              <h2 className="mb-2 text-sm font-semibold text-foreground">Aligned funnel counts</h2>
              <table className="w-full min-w-[620px] text-xs">
                <thead><tr>
                  <th className="p-2 text-left">Funnel stage</th>
                  {comparison.jobs.map((item) => <th className="p-2 text-left" key={item.id}>{item.name}</th>)}
                </tr></thead>
                <tbody>{comparisonStages.map((stage) => <tr key={stage.key} className="border-t border-border">
                  <th className="p-2 text-left font-medium">{stage.label}</th>
                  {comparison.jobs.map((item) => {
                    const level = item.funnel.find((candidate) => candidate.key === stage.key);
                    return <td className="p-2 tabular-nums" key={item.id}>{level && level.count != null ? level.count.toLocaleString() : "unavailable"}</td>;
                  })}
                </tr>)}</tbody>
              </table>
            </div>
            <div>
              <h2 className="mb-2 text-sm font-semibold text-foreground">MEV metrics</h2>
              <table className="w-full min-w-[620px] text-xs">
                <thead><tr>
                  <th className="p-2 text-left">Metric</th>
                  {comparison.jobs.map((item) => <th className="p-2 text-left" key={item.id}>{item.name}</th>)}
                </tr></thead>
                <tbody>{COMPARISON_METRICS.map(([key, label]) => <tr key={key} className="border-t border-border">
                  <th className="p-2 text-left font-medium">{label}</th>
                  {comparison.jobs.map((item) => <td className="p-2" key={item.id}>{formatComparisonMetric(item, key)}</td>)}
                </tr>)}</tbody>
              </table>
            </div>
          </div>
        )}
      </div>

      {isLoading && (
        <p className="rounded-xl border border-border bg-card p-8 text-center text-sm text-muted-foreground">
          Loading jobs…
        </p>
      )}

      {!isLoading && jobs.length === 0 && (
        <div className="rounded-xl border border-dashed border-border bg-card p-12 text-center">
          <p className="text-sm font-medium text-foreground">No jobs yet</p>
          <p className="mt-1 text-xs text-muted-foreground">
            Create your first pipeline job to start a real vaccine design run.
          </p>
          <Button asChild className="mt-4">
            <Link href="/jobs/new">
              <Plus className="h-4 w-4" />
              New Job
            </Link>
          </Button>
        </div>
      )}

      <div className="space-y-3">
        {jobs.map((job) => (
          <JobCard key={job.id} job={job} selected={selected.includes(job.id)} onToggle={() => toggleSelected(job.id)} />
        ))}
      </div>
    </div>
  );
}

function formatComparisonMetric(item: ComparisonJob, key: string): string {
  if (item.provenance.status === "unavailable") return "unavailable";
  const value = item.mevMetrics[key];
  if (value === undefined || value === null) return "unavailable";
  if (item.provenance.status === "local-analysis") {
    return `${String(value)} (local analysis; not externally validated)`;
  }
  if (item.provenance.status === "cached-real") return `${String(value)} (cached external result)`;
  return String(value);
}

function JobCard({ job, selected, onToggle }: { job: Job; selected: boolean; onToggle: () => void }) {
  const queryClient = useQueryClient();
  const { showToast } = useToast();

  const control = async (action: "pause" | "resume", label: string) => {
    try {
      await pipelineControl(job.id, action);
      showToast("Job updated", "success", `${job.name} — ${label}.`);
      queryClient.invalidateQueries({ queryKey: ["jobs"] });
    } catch (e) {
      showToast("Action failed", "error", e instanceof Error ? e.message : "Request failed");
    }
  };

  return (
    <Card>
      <CardContent className="p-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="flex min-w-0 items-start gap-3">
            <input aria-label={`Select ${job.name} for comparison`} type="checkbox" checked={selected} onChange={onToggle} disabled={job.status !== "completed"} className="mt-1 h-4 w-4" />
            <div className="min-w-0">
              <Link
                href={`/jobs/${job.id}`}
                className="text-sm font-semibold text-foreground hover:text-primary hover:underline"
              >
                {job.name}
              </Link>
              <StatusBadge status={job.status} />
              <Badge variant="outline" className="font-mono text-[10px]">
                {job.id}
              </Badge>
            </div>
            <p className="mt-1 text-xs text-muted-foreground">
              {job.pathogenName}
              {job.strain ? ` · ${job.strain}` : ""} · created {timeAgo(job.createdAt)} ·{" "}
              {job.status === "completed" ? `ran in ${formatDuration(job.elapsedSeconds)}` : `elapsed ${formatDuration(job.elapsedSeconds)}`}
            </p>
          </div>

          <div className="text-right">
            <p className="text-lg font-bold tabular-nums text-foreground">{job.progress}%</p>
            <p className="text-[11px] text-muted-foreground">
              Phase {job.currentPhase}/{job.phases.length}
            </p>
          </div>
        </div>

        <Progress
          value={job.progress}
          className="mt-3 h-2"
          indicatorClassName={
            job.status === "failed" ? "bg-red-500" : job.status === "paused" ? "bg-amber-500" : undefined
          }
        />

        <div className="mt-3 flex flex-wrap items-center justify-between gap-2">
          <p className="max-w-md truncate text-[11px] text-muted-foreground">
            {phaseNames(job).slice(0, job.currentPhase).join(" → ")}
            {job.status === "running" && " → …"}
          </p>
          <div className="flex gap-2">
            {job.status === "running" && (
              <Button size="sm" variant="secondary" onClick={() => control("pause", "stopped")}>
                Stop
              </Button>
            )}
            {job.status === "paused" && (
              <Button size="sm" onClick={() => control("resume", "resumed")}>
                Resume
              </Button>
            )}
            <Button asChild size="sm" variant="outline">
              <Link href={`/jobs/${job.id}`}>Open</Link>
            </Button>
          </div>
        </div>
      </CardContent>
    </Card>
  );
}