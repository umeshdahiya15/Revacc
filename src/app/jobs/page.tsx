"use client";

import Link from "next/link";
import { Plus } from "lucide-react";
import { useJobs } from "@/hooks/useJobs";
import { pipelineControl } from "@/lib/api";
import { useQueryClient } from "@tanstack/react-query";
import { formatDuration, timeAgo } from "@/lib/utils";
import { useToast } from "@/hooks/useToast";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import { StatusBadge } from "@/components/common/StatusBadge";
import type { Job } from "@/types";

const phaseNames = (job: Job) => job.phases.map((p) => p.name);

export default function JobsPage() {
  const { jobs, isLoading } = useJobs();

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
          <JobCard key={job.id} job={job} />
        ))}
      </div>
    </div>
  );
}

function JobCard({ job }: { job: Job }) {
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
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
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