"use client";

import { usePathname, useRouter } from "next/navigation";
import {
  BookOpen,
  ChevronLeft,
  ClipboardList,
  Dna,
  Home,
  Plus,
  Settings,
  Wrench,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { PIPELINE_PHASES } from "@/lib/constants";
import { usePipelineStore } from "@/store/pipelineStore";
import type { PhaseStatus } from "@/types";

const NAV_ITEMS = [
  { href: "/", label: "Home", icon: Home },
  { href: "/jobs/new", label: "New Job", icon: Plus },
  { href: "/jobs", label: "Jobs List", icon: ClipboardList },
  { href: "/tools", label: "Tools", icon: Wrench },
  { href: "/docs", label: "Docs", icon: BookOpen },
  { href: "/settings", label: "Settings", icon: Settings },
];

function useActiveJobId(): string | null {
  const pathname = usePathname() ?? "";
  const match = pathname.match(/^\/jobs\/([^/]+)/);
  return match?.[1] ?? null;
}

function PhaseRowStatus({ status }: { status: PhaseStatus }) {
  if (status === "running") {
    return (
      <span
        className="mev-pulse-dot h-2 w-2 shrink-0 rounded-full bg-blue-500"
        aria-hidden
      />
    );
  }
  const color =
    status === "completed"
      ? "bg-emerald-500"
      : status === "failed"
        ? "bg-red-500"
        : status === "paused"
          ? "bg-amber-500"
          : "bg-slate-600";
  return <span className={cn("h-2 w-2 shrink-0 rounded-full", color)} aria-hidden />;
}

export function Sidebar({
  collapsed,
  variant = "desktop",
  onToggle,
  onNavigate,
}: {
  collapsed: boolean;
  variant?: "desktop" | "drawer";
  onToggle?: () => void;
  onNavigate?: () => void;
}) {
  const pathname = usePathname() ?? "";
  const router = useRouter();
  const jobId = useActiveJobId();
  const storeJob = usePipelineStore((s) => s.activeJob);
  const job = storeJob && storeJob.id === jobId ? storeJob : undefined;

  const isDrawer = variant === "drawer";
  const showLabels = isDrawer || !collapsed;

  // On desktop: expanded = w-72; tablet always collapses to w-16 (icons only).
  const rootWidth = isDrawer
    ? "w-72"
    : collapsed
      ? "w-16"
      : "w-16 lg:w-72";
  const labelClass = cn("min-w-0", showLabels ? "lg:block hidden" : "hidden");
  const currentPhase = pathname.match(/phase\/(\d+)/)?.[1];

  const phaseHref = (phaseNumber: number) =>
    jobId ? `/jobs/${jobId}/phase/${phaseNumber}` : "/jobs";

  const go = (href: string) => {
    router.push(href);
    onNavigate?.();
  };

  return (
    <div
      className={cn(
        "flex h-full flex-col overflow-hidden bg-sidebar text-sidebar-foreground",
        rootWidth,
        "transition-[width] duration-200",
      )}
    >
      {/* Logo */}
      <div
        className={cn(
          "flex items-center gap-2.5 px-4 py-4",
          !showLabels && "justify-center px-2",
        )}
      >
        <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-primary text-primary-foreground">
          <Dna className="h-5 w-5" />
        </div>
        <div className={labelClass}>
            <p className="truncate text-sm font-semibold text-white">Revacc</p>
            <p className="truncate text-[11px] text-slate-500">Vaccine Design Suite</p>
        </div>
      </div>

      {/* Navigation */}
      <nav className="flex flex-col gap-0.5 px-2 py-2">
        {NAV_ITEMS.map((item) => {
          const active =
            item.href === "/" ? pathname === "/" : pathname.startsWith(item.href);
          const Icon = item.icon;
          return (
            <button
              key={item.href}
              onClick={() => go(item.href)}
              className={cn(
                "flex items-center gap-3 rounded-md px-2.5 py-2 text-left text-sm transition-colors",
                active
                  ? "bg-sidebar-accent text-sidebar-accent-foreground"
                  : "text-sidebar-foreground hover:bg-sidebar-accent hover:text-white",
                !showLabels && "justify-center px-0",
              )}
            >
              <Icon className="h-4.5 w-4.5 shrink-0" />
              <span className={cn("truncate", !showLabels && "hidden")}>{item.label}</span>
            </button>
          );
        })}
      </nav>

      <div className="px-4 pt-2">
        <div className={cn("h-px w-full bg-sidebar-border", !showLabels && "mx-2 w-auto")} />
      </div>

      {/* Pipeline phases */}
      <div className="flex min-h-0 flex-1 flex-col">
        <p
          className={cn(
            "truncate px-4 pb-1 pt-3 text-[11px] font-semibold uppercase tracking-wider text-sidebar-muted",
            !showLabels && "sr-only",
          )}
        >
          Pipeline Phases
        </p>
        <div className="mev-scroll flex-1 overflow-y-auto pb-2">
          {PIPELINE_PHASES.map((phase) => {
            const runtime = job?.phases.find((p) => p.number === phase.number);
            const status: PhaseStatus = runtime?.status ?? "pending";
            const Icon = phase.icon;
            const isActivePhase = currentPhase === String(phase.number);
            return (
              <button
                key={phase.number}
                onClick={() => go(phaseHref(phase.number))}
                title={
                  !showLabels ? `Phase ${phase.number} · ${phase.name}` : undefined
                }
                className={cn(
                  "group flex w-full items-center gap-2.5 border-l-2 px-3 py-1.5 text-left text-[13px]",
                  !showLabels && "justify-center px-0",
                  status === "completed" && "border-emerald-500",
                  status === "running" && "border-blue-500 bg-blue-500/10",
                  status === "failed" && "border-red-500",
                  status === "paused" && "border-amber-500",
                  status === "pending" && "border-transparent",
                  isActivePhase && "bg-sidebar-accent/60",
                )}
              >
                <span className="flex w-5 shrink-0">
                  <Icon
                    className={cn(
                      "h-4 w-4",
                      status === "pending" ? "text-sidebar-muted" : "text-sidebar-foreground",
                      isActivePhase && "text-white",
                    )}
                  />
                </span>
                <span className={cn("min-w-0 flex-1 truncate", !showLabels && "hidden")}>
                  {phase.name}
                </span>
                <PhaseRowStatus status={status} />
              </button>
            );
          })}
        </div>
      </div>

      {/* Bottom */}
      <div
        className={cn(
          "flex items-center gap-2.5 border-t border-sidebar-border px-4 py-3",
          !showLabels && "flex-col gap-2 px-0",
        )}
      >
        <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-sidebar-accent text-xs font-semibold text-white">
          RS
        </div>
        <div className={labelClass}>
          <p className="truncate text-xs font-medium text-white">Researcher</p>
          <p className="truncate text-[11px] text-sidebar-muted">
            immunoinformatics@lab
          </p>
        </div>
        <span className={cn("text-[10px] text-sidebar-muted", !showLabels && "hidden")}>
          v1.0
        </span>
        {onToggle && (
          <button
            onClick={onToggle}
            className="rounded-md p-1.5 text-sidebar-muted transition-colors hover:bg-sidebar-accent hover:text-white"
            aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
          >
            <ChevronLeft
              className={cn("h-4 w-4", collapsed && "rotate-180")}
            />
          </button>
        )}
      </div>
    </div>
  );
}