"use client";

import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { Activity, ShieldPlus, Syringe } from "lucide-react";
import type { ImmuneSimPoint } from "@/types";

const INJECTIONS = [1, 28, 56];

export function ImmuneSimCharts({ data }: { data: ImmuneSimPoint[] }) {
  const peakIgg = Math.max(...data.map((d) => d.igg ?? 0));
  const finalIgg = data[data.length - 1]?.igg ?? 0;

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <SummaryCard
          icon={<ShieldPlus className="h-4 w-4" />}
          label="Peak IgG (OD)"
          value={peakIgg.toFixed(1)}
          tone="text-blue-700"
        />
        <SummaryCard
          icon={<Activity className="h-4 w-4" />}
          label="Persistent IgG (day 350)"
          value={finalIgg.toFixed(1)}
          tone="text-emerald-700"
        />
        <SummaryCard
          icon={<Syringe className="h-4 w-4" />}
          label="Injections"
          value={String(INJECTIONS.length)}
          tone="text-amber-700"
        />
        <SummaryCard
          icon={<Activity className="h-4 w-4" />}
          label="IFN-γ peak (pg/mL)"
          value={`${Math.max(...data.map((d) => d.ifnGamma ?? 0)).toFixed(0)}`}
          tone="text-indigo-700"
        />
      </div>

      <div className="rounded-xl border border-border bg-card p-4">
        <h3 className="mb-1 text-sm font-semibold text-foreground">
          Humoral response — IgG & IgM
        </h3>
        <p className="mb-3 text-xs text-muted-foreground">
          Immunoglobulin titres across 350 simulation days. Dashed lines mark the 3
          immunizations.
        </p>
        <div className="h-72 w-full">
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={data} margin={{ top: 8, right: 12, left: -12, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#E2E8F0" />
              <XAxis
                dataKey="day"
                tick={{ fontSize: 11, fill: "#64748B" }}
                label={{ value: "Day", position: "insideBottom", offset: -2, fontSize: 11, fill: "#64748B" }}
              />
              <YAxis tick={{ fontSize: 11, fill: "#64748B" }} />
              <Tooltip
                contentStyle={{
                  borderRadius: 8,
                  border: "1px solid #E2E8F0",
                  fontSize: 12,
                }}
              />
              <Legend wrapperStyle={{ fontSize: 12 }} />
              {INJECTIONS.map((d) => (
                <ReferenceLine
                  key={d}
                  x={d}
                  stroke="#94A3B8"
                  strokeDasharray="5 5"
                  label={{ value: "↓", position: "top", fontSize: 12, fill: "#94A3B8" }}
                />
              ))}
              <Line
                type="monotone"
                dataKey="igg"
                name="IgG"
                stroke="#2563EB"
                strokeWidth={2.5}
                dot={false}
              />
              <Line
                type="monotone"
                dataKey="igm"
                name="IgM"
                stroke="#F59E0B"
                strokeWidth={2}
                dot={false}
              />
              <Line
                type="monotone"
                dataKey="iga"
                name="IgA"
                stroke="#8B5CF6"
                strokeWidth={1.5}
                dot={false}
              />
            </LineChart>
          </ResponsiveContainer>
        </div>
      </div>

      <div className="rounded-xl border border-border bg-card p-4">
        <h3 className="mb-1 text-sm font-semibold text-foreground">
          Cytokine response — IFN-γ & IL-2 (Th1)
        </h3>
        <p className="mb-3 text-xs text-muted-foreground">
          Simulated Th1-type cytokine secretion after each boost.
        </p>
        <div className="h-64 w-full">
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={data} margin={{ top: 8, right: 12, left: -12, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#E2E8F0" />
              <XAxis
                dataKey="day"
                tick={{ fontSize: 11, fill: "#64748B" }}
                label={{ value: "Day", position: "insideBottom", offset: -2, fontSize: 11, fill: "#64748B" }}
              />
              <YAxis tick={{ fontSize: 11, fill: "#64748B" }} />
              <Tooltip
                contentStyle={{
                  borderRadius: 8,
                  border: "1px solid #E2E8F0",
                  fontSize: 12,
                }}
              />
              <Legend wrapperStyle={{ fontSize: 12 }} />
              {INJECTIONS.map((d) => (
                <ReferenceLine key={d} x={d} stroke="#94A3B8" strokeDasharray="5 5" />
              ))}
              <Line
                type="monotone"
                dataKey="ifnGamma"
                name="IFN-γ"
                stroke="#10B981"
                strokeWidth={2.5}
                dot={false}
              />
              <Line
                type="monotone"
                dataKey="il2"
                name="IL-2"
                stroke="#6366F1"
                strokeWidth={2}
                dot={false}
              />
            </LineChart>
          </ResponsiveContainer>
        </div>
      </div>
    </div>
  );
}

function SummaryCard({
  icon,
  label,
  value,
  tone,
}: {
  icon: React.ReactNode;
  label: string;
  value: string;
  tone: string;
}) {
  return (
    <div className="rounded-lg border border-border bg-card p-3">
      <div className="flex items-center gap-1.5 text-muted-foreground">
        {icon}
        <p className="text-[11px] font-medium">{label}</p>
      </div>
      <p className={`mt-1 text-xl font-bold tabular-nums ${tone}`}>{value}</p>
    </div>
  );
}