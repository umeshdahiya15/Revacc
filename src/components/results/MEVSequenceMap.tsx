"use client";

import { useMemo, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { cn } from "@/lib/utils";
import { CopyButton } from "@/components/common/CopyButton";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import type { LinkerSegment } from "@/types";

type GroupKey = "pre" | "ctl" | "htl" | "bcell" | "post";

const BLOCK_STYLE: Record<string, { bg: string; text: string; chip?: boolean }> = {
  adjuvant: { bg: "bg-orange-500", text: "text-white" },
  ctl: { bg: "bg-blue-500", text: "text-white" },
  htl: { bg: "bg-emerald-500", text: "text-white" },
  bcell: { bg: "bg-purple-500", text: "text-white" },
  linker: { bg: "bg-slate-200", text: "text-slate-500", chip: true },
  other: { bg: "bg-slate-700", text: "text-white" },
};

function categorize(seg: LinkerSegment): GroupKey {
  if (seg.type === "adjuvant") return "pre";
  if (seg.type === "other") return seg.label.startsWith("His-tag") ? "pre" : "post";
  if (seg.type === "linker") {
    if (seg.label === "EAAAK") return "pre";
    if (seg.label.startsWith("GGGGS")) return "post";
    if (seg.label === "AAY") return "ctl";
    if (seg.label === "GPGPG") return "htl";
    if (seg.label === "KK") return "bcell";
    return "pre";
  }
  return seg.type as GroupKey;
}

export function MEVSequenceMap({
  sequence,
  linkerMap,
}: {
  sequence: string;
  linkerMap: LinkerSegment[];
}) {
  const [selected, setSelected] = useState<LinkerSegment | null>(null);

  const groupCaption = useMemo(() => {
    const ctlCount = linkerMap.filter(s => s.type === "ctl").length;
    const htlCount = linkerMap.filter(s => s.type === "htl").length;
    const bcellCount = linkerMap.filter(s => s.type === "bcell").length;
    return {
      pre: null,
      ctl: ctlCount > 0 ? `${ctlCount} CTL epitopes joined by AAY linkers` : null,
      htl: htlCount > 0 ? `${htlCount} HTL epitopes joined by GPGPG linkers` : null,
      bcell: bcellCount > 0 ? `${bcellCount} B-cell epitopes joined by KK linkers` : null,
      post: null,
    } as Record<GroupKey, string | null>;
  }, [linkerMap]);

  const rows = useMemo(() => {
    const out: LinkerSegment[][] = [];
    let current: LinkerSegment[] = [];
    let group: GroupKey | null = null;
    for (const seg of linkerMap) {
      const cat = categorize(seg);
      if (group !== null && cat !== group) {
        out.push(current);
        current = [];
      }
      group = cat;
      current.push(seg);
    }
    if (current.length) out.push(current);
    return out;
  }, [linkerMap]);

  const chunks = useMemo(() => {
    const out: { start: number; text: string }[] = [];
    for (let i = 0; i < sequence.length; i += 10) {
      out.push({ start: i + 1, text: sequence.slice(i, i + 10) });
    }
    return out;
  }, [sequence]);

  return (
    <div className="rounded-xl border border-border bg-card p-4">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <div>
          <h3 className="text-sm font-semibold text-foreground">MEV Construct Map</h3>
          <p className="text-xs text-muted-foreground">
            {sequence.length} aa · Adjuvant + linkers + 27 epitopes
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-3 text-[11px] text-muted-foreground">
          <span className="flex items-center gap-1.5">
            <span className="h-2.5 w-2.5 rounded-sm bg-orange-500" /> Adjuvant
          </span>
          <span className="flex items-center gap-1.5">
            <span className="h-2.5 w-2.5 rounded-sm bg-blue-500" /> CTL
          </span>
          <span className="flex items-center gap-1.5">
            <span className="h-2.5 w-2.5 rounded-sm bg-emerald-500" /> HTL
          </span>
          <span className="flex items-center gap-1.5">
            <span className="h-2.5 w-2.5 rounded-sm bg-purple-500" /> B-cell
          </span>
          <span className="flex items-center gap-1.5">
            <span className="h-2.5 w-2.5 rounded-sm bg-slate-300" /> Linker
          </span>
        </div>
      </div>

      <div className="space-y-2.5">
        {rows.map((row, ri) => {
          const caption = groupCaption[categorize(row[0])];
          return (
            <div key={ri} className="rounded-lg bg-slate-50 p-2.5">
              <div className="flex flex-wrap items-center gap-1.5">
                {row.map((seg) => (
                  <TooltipProvider key={`${seg.start}-${seg.end}`} delayDuration={150}>
                    <Tooltip>
                      <TooltipTrigger asChild>
                        <button
                          onClick={() =>
                            setSelected(selected?.start === seg.start ? null : seg)
                          }
                          className={cn(
                            "rounded-md px-2 py-1 text-[11px] font-semibold leading-none",
                            BLOCK_STYLE[seg.type].bg,
                            BLOCK_STYLE[seg.type].text,
                            BLOCK_STYLE[seg.type].chip && "px-1.5 py-0.5 text-[10px]",
                            "shadow-sm transition-transform hover:scale-105 focus:outline-none focus:ring-2 focus:ring-ring",
                            selected?.start === seg.start && "ring-2 ring-ring ring-offset-1",
                          )}
                        >
                          {seg.type === "linker" ? seg.label : seg.label}
                          {seg.type !== "linker" && (
                            <span className="ml-1 font-normal opacity-80">
                              {seg.end - seg.start + 1} aa
                            </span>
                          )}
                        </button>
                      </TooltipTrigger>
                      <TooltipContent className="max-w-xs">
                        <p className="font-mono text-[11px] break-all">{seg.sequence}</p>
                        <p className="mt-0.5 text-[10px] text-muted-foreground">
                          Positions {seg.start}–{seg.end} · {seg.end - seg.start + 1} residues
                        </p>
                      </TooltipContent>
                    </Tooltip>
                  </TooltipProvider>
                ))}
              </div>
              {caption && (
                <p className="mt-1.5 text-[10px] text-muted-foreground">{caption}</p>
              )}
            </div>
          );
        })}
      </div>

      <div className="mt-4">
        <div className="mb-1.5 flex items-center justify-between">
          <p className="text-xs font-medium text-muted-foreground">
            Full amino acid sequence
          </p>
          <CopyButton value={sequence} label="Copy" variant="ghost" />
        </div>
        <div className="mev-scroll overflow-x-auto rounded-lg border border-border bg-slate-50 p-3">
          <div className="flex w-max flex-wrap gap-x-1 font-mono text-[11px] leading-5">
            {chunks.map((chunk) => {
              const inSelected =
                selected &&
                chunk.start + chunk.text.length - 1 >= selected.start &&
                chunk.start <= selected.end;
              return (
                <span
                  key={chunk.start}
                  title={`pos ${chunk.start}`}
                  className={cn(
                    "rounded px-0.5",
                    inSelected
                      ? "bg-yellow-200 font-semibold text-slate-900"
                      : "text-slate-700",
                  )}
                >
                  {chunk.text}
                </span>
              );
            })}
          </div>
        </div>
        <div className="mt-1 flex items-center justify-between text-[10px] text-muted-foreground">
          <span>Positions in base 1 · chunks of 10</span>
          <span>Click a block to highlight its sequence</span>
        </div>
      </div>

      <AnimatePresence>
        {selected && (
          <motion.div
            initial={{ opacity: 0, y: 6 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0 }}
            className="mt-3 rounded-lg border border-blue-200 bg-blue-50 p-2.5 text-xs text-blue-800"
          >
            <span className="font-semibold">{selected.label}</span>
            <span className="mx-1.5 text-blue-400">·</span>
            <span className="font-mono break-all">{selected.sequence}</span>
            <span className="mx-1.5 text-blue-400">·</span>
            positions {selected.start}–{selected.end}
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}