"use client";

import { useState } from "react";
import { ChevronDown, Copy } from "lucide-react";
import { downloadFile } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import type { Step } from "@/types";

/**
 * Renders one step's real output: an organized summary of the raw result
 * plus an expandable raw-JSON dump. Handles success, skipped, and paused/
 * failed (error) states.
 */

const TONE: Record<string, string> = {
  pass: "border-emerald-200 bg-emerald-50/50",
  warn: "border-amber-200 bg-amber-50/50",
  fail: "border-red-200 bg-red-50/50",
  info: "border-blue-200 bg-blue-50/50",
};

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function formatValue(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "number") return String(value);
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (typeof value === "string") return value.length > 160 ? `${value.slice(0, 160)}…` : value;
  if (Array.isArray(value)) return `${value.length} item(s)`;
  return JSON.stringify(value);
}

function pickCards(result: Record<string, unknown>): { label: string; value: string; tone?: string }[] {
  const ordered = [
    ["count", "Count"],
    ["counts", "Counts"],
    ["total", "Total"],
    ["clusters", "Clusters"],
    ["proteins", "Proteins"],
    ["sequences", "Sequences"],
    ["nonRedundant", "Non-redundant"],
    ["removedAsRedundant", "Removed as redundant"],
    ["essential", "Essential"],
    ["removedAsNonEssential", "Removed as non-essential"],
    ["surface_exposed_count", "Surface-exposed"],
    ["transmembrane_count", "Transmembrane"],
    ["total_analyzed", "Analyzed"],
    ["predicted", "Predicted"],
    ["selected", "Selected"],
    ["status", "Status"],
    ["algorithm", "Algorithm"],
    ["method", "Method"],
    ["message", "Message"],
  ];
  const cards: { label: string; value: string; tone?: string }[] = [];
  for (const [key, label] of ordered) {
    if (key in result) {
      const val = result[key];
      if (isPlainObject(val)) continue;
      cards.push({ label, value: formatValue(val) });
    }
  }
  return cards.slice(0, 4);
}

export function StepResultPanel({ step }: { step: Step }) {
  const [open, setOpen] = useState(false);
  const result = step.result;
  const resultObj: Record<string, unknown> | undefined = isPlainObject(result) ? result : undefined;
  const cards = resultObj ? pickCards(resultObj) : [];
  const pausedBanner = resultObj && asPaused(resultObj);
  const skipped = step.status === "skipped";
  const raw =
    JSON.stringify(
      { status: step.status, result: result ?? null, error: step.error ?? null },
      null,
      2,
    ) ?? "";

  if (!result && !step.error && step.status !== "failed") {
    return (
      <div className="rounded-md border border-border bg-card px-3 py-2 text-[11px] text-muted-foreground">
        {skipped ? "Skipped — no result was produced." : "No result recorded for this step."}
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-2 rounded-md border border-border bg-card px-3 py-2.5">
      {step.status === "skipped" && (
        <p className="text-[11px] font-medium text-slate-500">
          Skipped — no result was produced. The pipeline continued to the next step.
        </p>
      )}

      {step.status === "failed" && step.error && (
        <div className={`rounded-md border px-3 py-2 ${
          step.error.severity === "warning"
            ? "border-amber-200 bg-amber-50/60"
            : step.error.severity === "pause"
              ? "border-orange-200 bg-orange-50/60"
              : "border-red-200 bg-red-50/60"
        }`}>
          <p className={`text-[11px] font-semibold ${
            step.error.severity === "warning"
              ? "text-amber-700"
              : step.error.severity === "pause"
                ? "text-orange-700"
                : "text-red-700"
          }`}>Error</p>
          <p className={`mt-0.5 text-[11px] ${
            step.error.severity === "warning"
              ? "text-amber-700"
              : step.error.severity === "pause"
                ? "text-orange-700"
                : "text-red-700"
          }`}>{step.error.message}</p>
          {step.error.retries > 0 && (
            <p className="mt-0.5 text-[10px] text-red-600">Retries: {step.error.retries}</p>
          )}
          {step.error.tool && (
            <p className="mt-0.5 text-[10px] text-slate-500">Tool: {step.error.tool}</p>
          )}
        </div>
      )}

      {pausedBanner && (
        <div className="rounded-md border border-amber-200 bg-amber-50/60 px-3 py-2">
          <p className="text-[11px] font-semibold text-amber-700">External tool unavailable</p>
          <p className="mt-0.5 text-[11px] text-amber-800">{pausedBanner.reason}</p>
          {pausedBanner.workaround && (
            <p className="mt-0.5 text-[10px] text-amber-700">Workaround: {pausedBanner.workaround}</p>
          )}
        </div>
      )}

      {cards.length > 0 && (
        <div className="grid grid-cols-2 gap-1.5 sm:grid-cols-4">
          {cards.map((card) => (
            <div key={card.label} className={cn("rounded-md border px-2 py-1.5", TONE[card.tone ?? "info"] ?? TONE.info)}>
              <p className="truncate text-[10px] font-medium text-muted-foreground">{card.label}</p>
              <p className="mt-0.5 truncate text-sm font-bold tabular-nums text-foreground">{card.value}</p>
            </div>
          ))}
        </div>
      )}

      <div>
        <button
          onClick={() => setOpen((v) => !v)}
          className="flex items-center gap-1 text-[10px] font-medium uppercase tracking-wide text-muted-foreground transition-colors hover:text-foreground"
        >
          <ChevronDown className={cn("h-3 w-3 transition-transform", open && "rotate-180")} />
          Raw data ({step.id})
        </button>
        {open && (
          <div className="mt-1.5 flex flex-col gap-1.5">
            <pre className="max-h-72 overflow-auto rounded-md bg-slate-950 p-3 text-[10px] leading-relaxed text-slate-200">
              {raw}
            </pre>
            <Button
              size="sm"
              variant="outline"
              className="self-start gap-1"
              onClick={() => {
                downloadFile(raw, `step-${step.id}-raw.json`, "application/json");
              }}
            >
              <Copy className="h-3 w-3" />
              Download JSON
            </Button>
            {resultObj && cards.length > 0 && (
              <p className="text-[11px] text-muted-foreground">
                {plusCount(resultObj)}
              </p>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

function asPaused(result: Record<string, unknown>) {
  if (result._paused === true) {
    return {
      reason: typeof result.reason === "string" ? result.reason : String(result.tool ?? "tool unavailable"),
      workaround: typeof result.workaround === "string" ? result.workaround : undefined,
    };
  }
  return null;
}

function plusCount(result: Record<string, unknown>): string {
  const primitives = Object.entries(result).filter(([, v]) => !isPlainObject(v) && !Array.isArray(v));
  const arrays = Object.entries(result).filter(([, v]) => Array.isArray(v));
  const objects = Object.entries(result).filter(([, v]) => isPlainObject(v));
  const bits: string[] = [];
  if (arrays.length) bits.push(`${arrays.length} arrays`);
  if (objects.length) bits.push(`${objects.length} nested objects`);
  if (primitives.length) bits.push(`${primitives.length} scalar fields`);
  return bits.length ? `Full result: ${bits.join(", ")}.` : "";
}