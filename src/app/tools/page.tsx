"use client";

import { useState } from "react";
import { ExternalLink, Globe, HardDrive, Search } from "lucide-react";
import { TOOL_REGISTRY } from "@/lib/constants";
import { cn } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Card, CardContent } from "@/components/ui/card";
import type { ApiKind } from "@/types";

const API_KIND_META: Record<ApiKind, { label: string; icon: React.ReactNode }> = {
  api: { label: "REST API", icon: <Globe className="h-3.5 w-3.5" /> },
  scrape: { label: "Web scrape", icon: <Globe className="h-3.5 w-3.5" /> },
  local: { label: "Local tool", icon: <HardDrive className="h-3.5 w-3.5" /> },
};

const AVAILABILITY_META: Record<string, { label: string; tone: string }> = {
  online: { label: "Online", tone: "bg-emerald-500" },
  rate_limited: { label: "Rate-limited", tone: "bg-amber-500" },
  local: { label: "Local", tone: "bg-blue-500" },
  scrape: { label: "Scraped", tone: "bg-violet-500" },
};

export default function ToolsPage() {
  const [query, setQuery] = useState("");
  const [phaseFilter, setPhaseFilter] = useState<number | "all">("all");

  const filtered = TOOL_REGISTRY.filter((t) => {
    const q = query.trim().toLowerCase();
    const matchQuery =
      !q || t.name.toLowerCase().includes(q) || t.usedFor.toLowerCase().includes(q) || t.id.includes(q);
    const matchPhase = phaseFilter === "all" || t.phase === phaseFilter;
    return matchQuery && matchPhase;
  });

  const phases = Array.from(new Set(TOOL_REGISTRY.map((t) => t.phase))).sort((a, b) => a - b);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-foreground">Tool Registry</h1>
        <p className="text-sm text-muted-foreground">
          {TOOL_REGISTRY.length} tools across the 14-phase pipeline — external APIs, scraped web tools and
          local executables
        </p>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <div className="relative flex-1 sm:max-w-xs">
          <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search tools…"
            className="pl-9"
          />
        </div>
        <div className="flex flex-wrap gap-1.5">
          <Badge
            variant={phaseFilter === "all" ? "default" : "outline"}
            className="cursor-pointer select-none"
            onClick={() => setPhaseFilter("all")}
          >
            All phases
          </Badge>
          {phases.map((p) => (
            <Badge
              key={p}
              variant={phaseFilter === p ? "default" : "outline"}
              className="cursor-pointer select-none"
              onClick={() => setPhaseFilter(phaseFilter === p ? "all" : p)}
            >
              P{p}
            </Badge>
          ))}
        </div>
      </div>

      <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
        {filtered.map((tool) => {
          const kind = API_KIND_META[tool.apiKind];
          const avail = AVAILABILITY_META[tool.availability] ?? { label: tool.availability, tone: "bg-slate-400" };
          return (
            <Card key={tool.id}>
              <CardContent className="p-4">
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    <p className="truncate text-sm font-semibold text-foreground">{tool.name}</p>
                    <p className="mt-0.5 text-[11px] text-muted-foreground">{tool.usedFor}</p>
                  </div>
                  {tool.endpoint ? (
                    <a
                      href={tool.endpoint}
                      target="_blank"
                      rel="noreferrer"
                      className="mt-0.5 shrink-0 rounded-md border border-border p-1 text-muted-foreground transition-colors hover:border-primary hover:text-primary"
                    >
                      <ExternalLink className="h-3.5 w-3.5" />
                    </a>
                  ) : (
                    <span className="mt-0.5 shrink-0 text-muted-foreground/40">
                      <HardDrive className="h-3.5 w-3.5" />
                    </span>
                  )}
                </div>
                <div className="mt-3 flex flex-wrap items-center gap-1.5">
                  <Badge variant="outline" className="font-mono text-[10px]">Phase {tool.phase}</Badge>
                  <Badge variant="secondary" className="gap-1 text-[10px]">{kind.icon}{kind.label}</Badge>
                  <span className="inline-flex items-center gap-1 rounded-full bg-muted px-2 py-0.5 text-[10px] font-medium text-foreground">
                    <span className={cn("h-1.5 w-1.5 rounded-full", avail.tone)} />
                    {avail.label}
                  </span>
                </div>
              </CardContent>
            </Card>
          );
        })}
        {filtered.length === 0 && (
          <p className="col-span-full rounded-xl border border-border bg-card p-8 text-center text-sm text-muted-foreground">
            No tools match your filters.
          </p>
        )}
      </div>
    </div>
  );
}