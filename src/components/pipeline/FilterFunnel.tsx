"use client";

import { useEffect, useRef, useState } from "react";
import { ArrowDown, CheckCircle2, Filter } from "lucide-react";
import { motion } from "framer-motion";
import { cn } from "@/lib/utils";
import type { FilterFunnelLevel } from "@/types";

function useCountUp(target: number, duration = 600): number {
  const [value, setValue] = useState(target);
  const fromRef = useRef(target);

  useEffect(() => {
    const from = fromRef.current;
    if (from === target) return;
    const start = performance.now();
    let raf = 0;
    const tick = (now: number) => {
      const t = Math.min(1, (now - start) / duration);
      const eased = 1 - Math.pow(1 - t, 3);
      setValue(from + (target - from) * eased);
      if (t < 1) raf = requestAnimationFrame(tick);
      else fromRef.current = target;
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [target, duration]);

  return value;
}

function FunnelLevel({
  level,
  max,
  levelsLength,
  index,
}: {
  level: FilterFunnelLevel;
  max: number;
  levelsLength: number;
  index: number;
}) {
  const unavailable = level.count == null;
  const numericCount = level.count ?? 0;
  const count = useCountUp(numericCount);
  const widthPct = unavailable ? 0 : Math.max(8, (numericCount / max) * 100);

  return (
    <div className="space-y-1">
      <motion.div
        layout
        className="flex items-center gap-3 rounded-lg border border-border bg-card px-3 py-2.5"
      >
        {level.final ? (
          <CheckCircle2 className="h-4.5 w-4.5 shrink-0 text-emerald-500" />
        ) : (
          <Filter className="h-4 w-4 shrink-0 text-slate-400" />
        )}
        <div className="min-w-0 flex-1">
          <div className="flex items-baseline justify-between gap-2">
            <p
              className={cn(
                "truncate text-xs font-medium",
                level.final ? "text-emerald-700" : "text-slate-700",
              )}
            >
              {level.label}
            </p>
            <p className="shrink-0 text-sm font-bold tabular-nums text-foreground">
              {unavailable ? "unavailable" : Math.round(count).toLocaleString()}
            </p>
          </div>
          <div className="mt-1.5 h-1.5 w-full overflow-hidden rounded-full bg-slate-100">
            <motion.div
              className={cn(
                "h-full rounded-full",
                unavailable ? "bg-amber-400" : level.final ? "bg-emerald-500" : "bg-blue-500",
              )}
              initial={false}
              animate={{ width: `${widthPct}%` }}
              transition={{ duration: 0.6, ease: "easeOut" }}
            />
          </div>
          <div className="mt-0.5 flex items-center justify-between">
            <p className="text-[10px] text-muted-foreground">
              {unavailable ? "not available from this run" : level.filterLabel ?? "retrieved"}
            </p>
            {!level.final && (
              <p className="text-[10px] text-slate-400">↓ filtered</p>
            )}
          </div>
        </div>
      </motion.div>
      {index < levelsLength - 1 && (
        <div className="flex justify-center py-0.5">
          <ArrowDown className="h-3.5 w-3.5 text-slate-300" />
        </div>
      )}
    </div>
  );
}

export function FilterFunnel({
  levels,
  title = "Protein / Epitope Filtering Funnel",
}: {
  levels: FilterFunnelLevel[];
  title?: string;
}) {
  const max = Math.max(...levels.map((l) => l.count ?? 0), 1);

  return (
    <div className="rounded-xl border border-border bg-card p-4">
      <div className="mb-3 flex items-center justify-between">
        <h2 className="text-sm font-semibold text-foreground">{title}</h2>
        <span className="text-[11px] font-medium text-muted-foreground">
          live counts
        </span>
      </div>
      <div className="grid grid-cols-1 gap-1 md:grid-cols-2 xl:grid-cols-4">
        {levels.map((level, i) => (
          <FunnelLevel
            key={level.key}
            level={level}
            max={max}
            levelsLength={levels.length}
            index={i}
          />
        ))}
      </div>
    </div>
  );
}