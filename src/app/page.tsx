"use client";

import Link from "next/link";
import { motion } from "framer-motion";
import {
  AlertTriangle,
  ArrowRight,
  CheckCircle2,
  CircleDashed,
  History,
  Loader2,
  Plus,
  XCircle,
} from "lucide-react";
import { useJobs } from "@/hooks/useJobs";
import { useActivity } from "@/hooks/useActivity";
import { formatDuration, timeAgo, cn } from "@/lib/utils";
import { pipelineControl } from "@/lib/api";
import { useQueryClient } from "@tanstack/react-query";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import { StatusBadge } from "@/components/common/StatusBadge";
import { useToast } from "@/hooks/useToast";
import type { ActivityEntry, Job } from "@/types";

const ACTIVITY_ICON: Record<ActivityEntry["kind"], React.ReactNode> = {
  success: <CheckCircle2 className="h-4 w-4 text-emerald-500" />,
  running: <Loader2 className="h-4 w-4 animate-spin text-blue-500" />,
  error: <XCircle className="h-4 w-4 text-red-500" />,
  warning: <AlertTriangle className="h-4 w-4 text-amber-500" />,
  info: <CircleDashed className="h-4 w-4 text-slate-400" />,
};

export default function DashboardPage() {
  const queryClient = useQueryClient();
  const { showToast } = useToast();
  const { jobs, isLoading } = useJobs();
  const { entries } = useActivity();

  const stats = {
    total: jobs.length,
    running: jobs.filter((j) => j.status === "running").length,
    completed: jobs.filter((j) => j.status === "completed").length,
    failed: jobs.filter((j) => j.status === "failed" || j.status === "paused").length,
  };

  const activeJobs = jobs.filter((j) => j.status !== "completed");
  const recentJobs = jobs.filter((j) => j.status === "completed");

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ["jobs"] });

  const control = async (job: Job, action: "pause" | "resume", label: string) => {
    try {
      await pipelineControl(job.id, action);
      showToast("Job updated", "success", `${job.name} — ${label}.`);
      invalidate();
    } catch (e) {
      showToast("Action failed", "error", e instanceof Error ? e.message : "Request failed");
    }
  };

  const statCards = [
    { label: "Total Jobs", value: stats.total, icon: "📊", tone: "text-slate-900" },
    { label: "Running Now", value: stats.running, icon: "🔵", tone: "text-blue-600" },
    { label: "Completed", value: stats.completed, icon: "🟢", tone: "text-emerald-600" },
    { label: "Failed / Paused", value: stats.failed, icon: "⚠️", tone: "text-red-500" },
  ];

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold text-foreground">Dashboard</h1>
          <p className="text-sm text-muted-foreground">
            Reverse-vaccinology vaccine design pipeline — job overview
          </p>
        </div>
        <Button asChild>
          <Link href="/jobs/new">
            <Plus className="h-4 w-4" />
            New Job
          </Link>
        </Button>
      </div>

      {/* Stat cards */}
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        {statCards.map((card, i) => (
          <motion.div
            key={card.label}
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: i * 0.05 }}
          >
            <Card>
              <CardContent className="p-4">
                <div className="flex items-center justify-between">
                  <p className="text-xs font-medium text-muted-foreground">{card.label}</p>
                  <span className="text-sm">{card.icon}</span>
                </div>
                <p className={cn("mt-1 text-3xl font-bold tabular-nums", card.tone)}>
                  {isLoading ? "…" : card.value}
                </p>
              </CardContent>
            </Card>
          </motion.div>
        ))}
      </div>

      <div className="grid grid-cols-1 gap-6 xl:grid-cols-5">
        {/* Pipeline jobs */}
        <div className="space-y-4 xl:col-span-3">
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-base">Pipeline Jobs</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              {isLoading && (
                <p className="py-6 text-center text-sm text-muted-foreground">Loading jobs…</p>
              )}
              {!isLoading && jobs.length === 0 && (
                <div className="rounded-lg border border-dashed border-border p-8 text-center">
                  <p className="text-sm font-medium text-foreground">No jobs yet</p>
                  <p className="mt-1 text-xs text-muted-foreground">
                    Create a job to start a real 14-phase vaccine design run.
                  </p>
                  <Button asChild size="sm" className="mt-4">
                    <Link href="/jobs/new">
                      <Plus className="h-3.5 w-3.5" />
                      New Job
                    </Link>
                  </Button>
                </div>
              )}
              {[...activeJobs, ...recentJobs].map((job) => (
                <ActiveJobRow
                  key={job.id}
                  job={job}
                  onControl={(action, label) => control(job, action, label)}
                />
              ))}
            </CardContent>
          </Card>
        </div>

        {/* Recent activity */}
        <div className="xl:col-span-2">
          <Card className="h-full">
            <CardHeader className="pb-2">
              <CardTitle className="flex items-center gap-2 text-base">
                <History className="h-4 w-4 text-muted-foreground" />
                Recent Activity
              </CardTitle>
            </CardHeader>
            <CardContent>
              {entries.length === 0 ? (
                <p className="py-6 text-center text-xs text-muted-foreground">
                  No pipeline activity yet — events appear here as jobs run.
                </p>
              ) : (
                <ol className="relative space-y-3 before:absolute before:left-[7px] before:top-1 before:bottom-1 before:w-px before:bg-border">
                  {entries.slice(0, 10).map((entry) => (
                    <li key={entry.id} className="relative flex items-start gap-3 pl-0">
                      <span className="relative z-10 flex h-4 w-4 items-center justify-center rounded-full bg-background">
                        {ACTIVITY_ICON[entry.kind]}
                      </span>
                      <div className="min-w-0">
                        <p className="text-xs text-foreground">{entry.message}</p>
                        <p className="mt-0.5 text-[11px] text-muted-foreground">
                          {timeAgo(entry.timestamp)} · {entry.jobName}
                        </p>
                      </div>
                    </li>
                  ))}
                </ol>
              )}
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  );
}

function ActiveJobRow({
  job,
  onControl,
}: {
  job: Job;
  onControl: (action: "pause" | "resume", label: string) => void;
}) {
  const isPaused = job.status === "paused";
  return (
    <div className="rounded-lg border border-border p-3 transition-shadow hover:shadow-sm">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="min-w-0">
          <p className="truncate text-sm font-semibold text-foreground">{job.name}</p>
          <p className="text-[11px] text-muted-foreground">
            <span className="font-medium text-slate-600">{job.pathogenName}</span>
            {job.strain ? ` · ${job.strain}` : ""}
          </p>
        </div>
        <StatusBadge status={job.status} />
      </div>

      <div className="mt-2 flex items-center gap-3">
        <Progress
          value={job.progress}
          className="h-2 flex-1"
          indicatorClassName={cn(
            job.status === "completed" && "bg-emerald-500",
            job.status === "failed" && "bg-red-500",
            job.status === "paused" && "bg-amber-500",
          )}
        />
        <span className="w-10 shrink-0 text-right text-xs font-semibold tabular-nums text-foreground">
          {job.progress}%
        </span>
      </div>

      <div className="mt-2 flex flex-wrap items-center justify-between gap-2 text-[11px] text-muted-foreground">
        <span className="font-medium text-slate-600">
          Phase {job.currentPhase}/{job.phases.length}
          {job.status === "running" && <> · {job.phases[job.currentPhase - 1]?.name}</>}
        </span>
        <span>
          {job.status === "running"
            ? `Elapsed ${formatDuration(job.elapsedSeconds)}`
            : job.status === "paused"
              ? "Paused — resume to continue"
              : job.status === "completed"
                ? `${job.stepsCompleted}/${job.totalSteps} steps completed`
                : `Elapsed ${formatDuration(job.elapsedSeconds)}`}
        </span>
      </div>

      <div className="mt-2.5 flex items-center gap-2">
        <Button asChild size="sm" variant="outline">
          <Link href={`/jobs/${job.id}`}>
            View
            <ArrowRight className="h-3.5 w-3.5" />
          </Link>
        </Button>

        {job.status === "running" && (
          <Button size="sm" variant="secondary" onClick={() => onControl("pause", "stopped")}>
            <XCircle className="h-3.5 w-3.5" />
            Stop
          </Button>
        )}
        {isPaused && (
          <Button size="sm" onClick={() => onControl("resume", "resumed")}>
            Resume
          </Button>
        )}
        {job.status === "completed" && (
          <Badge variant="outline" className="gap-1 rounded-md py-1 text-emerald-700">
            <CheckCircle2 className="h-3 w-3" />
            Completed
          </Badge>
        )}
      </div>
    </div>
  );
}