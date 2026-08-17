"use client";

import { usePathname, useRouter } from "next/navigation";
import { Bell, ChevronRight, Menu } from "lucide-react";
import { cn } from "@/lib/utils";
import { usePipelineStore } from "@/store/pipelineStore";
import { Badge } from "@/components/ui/badge";
import type { Job } from "@/types";

function buildCrumbs(pathname: string, jobs: Job[]): { label: string; href?: string }[] {
  const parts = pathname.split("/").filter(Boolean);
  if (parts.length === 0) return [{ label: "Dashboard", href: "/" }];

  const crumbs: { label: string; href?: string }[] = [{ label: "Home", href: "/" }];

  if (parts[0] === "jobs") {
    crumbs.push({ label: "Jobs", href: "/jobs" });
    if (parts[1] && parts[1] !== "new") {
      const job = jobs.find((j) => j.id === parts[1]);
      crumbs.push({
        label: job?.name ?? parts[1],
        href: `/jobs/${parts[1]}`,
      });
      if (parts[2] === "phase" && parts[3]) {
        crumbs.push({ label: `Phase ${parts[3]}` });
      }
    }
    if (parts[1] === "new") crumbs.push({ label: "New Job" });
  } else if (parts[0] === "tools") {
    crumbs.push({ label: "Tools" });
  } else if (parts[0] === "settings") {
    crumbs.push({ label: "Settings" });
  } else if (parts[0] === "docs") {
    crumbs.push({ label: "Docs" });
  }

  return crumbs;
}

export function Header({ onMenuClick }: { onMenuClick: () => void }) {
  const pathname = usePathname() ?? "";
  const router = useRouter();
  const isConnected = usePipelineStore((s) => s.isConnected);
  const jobs = usePipelineStore((s) => s.jobs);
  const crumbs = buildCrumbs(pathname, jobs);
  const isJobPage = /^\/jobs\//.test(pathname);

  return (
    <header className="sticky top-0 z-30 flex h-14 shrink-0 items-center gap-3 border-b border-border bg-background/95 px-4 backdrop-blur md:px-6">
      <button
        onClick={onMenuClick}
        className="rounded-md p-2 text-muted-foreground hover:bg-muted md:hidden"
        aria-label="Open navigation"
      >
        <Menu className="h-5 w-5" />
      </button>

      <nav className="flex min-w-0 items-center gap-1.5 text-sm" aria-label="Breadcrumb">
        {crumbs.map((crumb, i) => {
          const last = i === crumbs.length - 1;
          return (
            <span key={crumb.label} className="flex min-w-0 items-center gap-1.5">
              {i > 0 && <ChevronRight className="h-3.5 w-3.5 shrink-0 text-muted-foreground/60" />}
              {last || !crumb.href ? (
                <span className="truncate font-medium text-foreground">{crumb.label}</span>
              ) : (
                <button
                  onClick={() => crumb.href && router.push(crumb.href)}
                  className="truncate text-muted-foreground transition-colors hover:text-foreground"
                >
                  {crumb.label}
                </button>
              )}
            </span>
          );
        })}
      </nav>

      <div className="ml-auto flex items-center gap-2">
        {isJobPage && (
          <Badge
            variant="outline"
            className={cn(
              "hidden items-center gap-1.5 border-border px-2.5 py-1 font-normal sm:inline-flex",
            )}
          >
            <span
              className={cn(
                "h-2 w-2 rounded-full",
                isConnected ? "mev-pulse-dot bg-emerald-500" : "bg-slate-300",
              )}
            />
            <span className="text-xs text-muted-foreground">
              {isConnected ? "Live updates" : "Polling"}
            </span>
          </Badge>
        )}
        <button
          className="rounded-md p-2 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
          aria-label="Notifications"
        >
          <Bell className="h-4.5 w-4.5" />
        </button>
        <div className="flex h-8 w-8 items-center justify-center rounded-full bg-primary/10 text-xs font-semibold text-primary">
          RS
        </div>
      </div>
    </header>
  );
}