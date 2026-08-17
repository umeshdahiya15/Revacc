"use client";

import { useState } from "react";
import { Globe } from "lucide-react";
import type { PopulationCoverage } from "@/types";

const REGION_PATHS: Record<string, string[]> = {
  "North America": [
    "M -140 70 L -90 72 L -70 62 L -62 55 L -80 22 L -95 15 L -110 20 L -130 30 L -140 48 L -160 58 Z",
  ],
  "Central America": [
    "M -95 12 L -88 10 L -80 8 L -85 4 L -93 6 Z",
  ],
  "South America": [
    "M -78 12 L -58 8 L -35 -5 L -40 -20 L -50 -35 L -58 -52 L -70 -48 L -80 -30 L -82 -12 Z",
  ],
  Europe: [
    "M -9 58 L 4 62 L 18 68 L 35 66 L 42 60 L 38 47 L 28 40 L 12 44 L 0 46 L -10 50 Z",
  ],
  "North Africa": [
    "M -15 30 L 5 35 L 30 35 L 35 28 L 25 22 L 12 16 L 0 12 L -12 20 Z",
  ],
  "Sub-Saharan Africa": [
    "M -17 20 L 5 15 L 25 12 L 42 8 L 50 -5 L 42 -25 L 30 -34 L 16 -30 L 2 -24 L -10 -15 L -16 -2 Z",
  ],
  "West Asia": [
    "M 40 40 L 54 36 L 60 30 L 55 24 L 48 14 L 40 20 L 35 28 Z",
  ],
  "South Asia": [
    "M 68 34 L 82 35 L 90 28 L 86 10 L 76 6 L 68 20 L 65 26 Z",
  ],
  "East Asia": [
    "M 120 40 L 135 45 L 130 28 L 122 20 L 105 22 L 95 28 L 105 34 Z",
  ],
  "Southeast Asia": [
    "M 95 8 L 108 6 L 118 4 L 122 -2 L 112 -10 L 100 -10 L 92 -2 Z",
  ],
  Oceania: [
    "M 130 -12 L 146 -10 L 156 -16 L 164 -24 L 162 -38 L 152 -45 L 136 -36 L 122 -28 L 112 -16 Z",
  ],
  "Northeast Asia": [
    "M 118 52 L 132 56 L 150 62 L 142 52 L 126 46 L 118 48 Z",
  ],
};

function hexToRgb(hex: string): [number, number, number] {
  const n = parseInt(hex.slice(1), 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

function interpolate(hexA: string, hexB: string, t: number): string {
  const a = hexToRgb(hexA);
  const b = hexToRgb(hexB);
  const r = Math.round(a[0] + (b[0] - a[0]) * t);
  const g = Math.round(a[1] + (b[1] - a[1]) * t);
  const bl = Math.round(a[2] + (b[2] - a[2]) * t);
  return `rgb(${r},${g},${bl})`;
}

const LOW_COLOR = "#DBEAFE";
const HIGH_COLOR = "#1D4ED8";

export function PopulationCoverageMap({
  coverage,
}: {
  coverage: PopulationCoverage;
}) {
  const [hovered, setHovered] = useState<string | null>(null);

  const min = 80;
  const max = 100;
  const colorFor = (value: number) => interpolate(LOW_COLOR, HIGH_COLOR, Math.min(1, Math.max(0, (value - min) / (max - min))));

  return (
    <div className="rounded-xl border border-border bg-card p-4">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <Globe className="h-4 w-4 text-primary" />
          <h3 className="text-sm font-semibold text-foreground">Population Coverage</h3>
        </div>
        <div className="flex items-center gap-2">
          <div className="flex items-center gap-1 text-[10px] text-muted-foreground">
            <span>{min}%</span>
            <div className="h-2 w-24 rounded-full" style={{ background: `linear-gradient(to right, ${LOW_COLOR}, ${HIGH_COLOR})` }} />
            <span>{max}%</span>
          </div>
        </div>
      </div>

      <div className="relative overflow-hidden rounded-lg border border-slate-200 bg-slate-50">
        <svg viewBox="-170 -80 340 165" className="h-auto w-full">
          {REGION_PATHS["North America"] &&
            Object.entries(REGION_PATHS).map(([region, paths]) => {
              const data = coverage.regions.find((r) => r.region === region);
              const fill = data ? colorFor(data.coverage) : "#E2E8F0";
              const isHovered = hovered === region;
              return (
                <g
                  key={region}
                  onMouseEnter={() => setHovered(region)}
                  onMouseLeave={() => setHovered(null)}
                  className="cursor-pointer"
                >
                  {paths.map((d, i) => (
                    <path
                      key={i}
                      d={d}
                      fill={fill}
                      stroke="#ffffff"
                      strokeWidth={0.8}
                      opacity={isHovered ? 1 : 0.92}
                    />
                  ))}
                </g>
              );
            })}
        </svg>

        {hovered && (() => {
          const data = coverage.regions.find((r) => r.region === hovered);
          return (
            <div className="pointer-events-none absolute left-2 top-2 rounded-md bg-white/95 px-2.5 py-1.5 shadow-md">
              <p className="text-[11px] font-semibold text-foreground">{hovered}</p>
              {data ? (
                <p className="text-[10px] text-muted-foreground">
                  {data.coverage}% · {(data.populations / 1_000_000_000).toFixed(1)}B population covered
                </p>
              ) : (
                <p className="text-[10px] text-muted-foreground">No coverage data</p>
              )}
            </div>
          );
        })()}
      </div>

      <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-4">
        <div className="col-span-2 rounded-lg border border-blue-200 bg-blue-50 p-3 text-center sm:col-span-1">
          <p className="text-2xl font-bold tabular-nums text-blue-700">
            {coverage.global.toFixed(2)}%
          </p>
          <p className="text-[11px] text-blue-600">Global population coverage</p>
        </div>
        {[...coverage.regions]
          .sort((a, b) => b.coverage - a.coverage)
          .slice(0, 8)
          .map((r) => (
            <div
              key={r.region}
              className="rounded-lg border border-border bg-card px-3 py-2"
            >
              <div className="flex items-center justify-between">
                <p className="truncate text-[11px] font-medium text-foreground">{r.region}</p>
                <p className="ml-1 text-[11px] font-semibold tabular-nums text-slate-700">
                  {r.coverage}%
                </p>
              </div>
              <div className="mt-1.5 h-1.5 w-full overflow-hidden rounded-full bg-slate-100">
                <div
                  className="h-full rounded-full"
                  style={{ width: `${r.coverage}%`, backgroundColor: colorFor(r.coverage) }}
                />
              </div>
            </div>
          ))}
      </div>
    </div>
  );
}