"use client";

import { useMemo, useState } from "react";
import { DataTable, type RowData } from "@/components/results/DataTable";
import { Badge } from "@/components/ui/badge";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { cn } from "@/lib/utils";
import type { Epitope } from "@/types";

const TYPE_BADGE: Record<Epitope["type"], { label: string; className: string }> = {
  CTL: { label: "CTL", className: "bg-blue-100 text-blue-700" },
  HTL: { label: "HTL", className: "bg-emerald-100 text-emerald-700" },
  BCELL_LINEAR: { label: "B-cell", className: "bg-purple-100 text-purple-700" },
  BCELL_CONFORMATIONAL: { label: "B-cell conf.", className: "bg-violet-100 text-violet-700" },
};

export function EpitopeTable({
  epitopes,
  defaultType,
  filename = "epitopes.csv",
  showFilter = true,
}: {
  epitopes: Epitope[];
  defaultType?: Epitope["type"];
  filename?: string;
  showFilter?: boolean;
}) {
  const [type, setType] = useState<Epitope["type"] | "ALL">(defaultType ?? "ALL");

  const filtered = useMemo(
    () => (type === "ALL" ? epitopes : epitopes.filter((e) => e.type === type)),
    [epitopes, type],
  );

  const rows: RowData[] = filtered.map((e, i) => ({
    "#": i + 1,
    "Epitope Sequence": e.sequence,
    "Source Protein": e.sourceProteinName ?? e.sourceProtein,
    "Position": e.startPosition ?? "—",
    "HLA Allele": e.hlaAllele ?? e.hlaAlleles?.join(", ") ?? "—",
    "Method": e.predictionMethod ?? "—",
    "IC50 (nM)": e.ic50?.toFixed(1) ?? "—",
    "Percentile Rank": e.percentileRank?.toFixed(2) ?? "—",
    "Antigenicity": e.antigenicityScore?.toFixed(2) ?? "—",
    "Immunogenicity": e.immunogenicityScore?.toFixed(2) ?? "—",
    "Toxic": e.isToxic ? "Yes" : "No",
    "Allergenic": e.isAllergenic ? "Yes" : "No",
    "IFN-γ": e.cytokineProfile?.ifnGamma ?? "—",
    "IL-4": e.cytokineProfile?.il4 ?? "—",
    "IL-10": e.cytokineProfile?.il10 ?? "—",
  }));

  return (
    <div className="space-y-3">
      {showFilter && (
        <div className="flex flex-wrap items-center justify-between gap-2">
          <Tabs
            value={type}
            onValueChange={(v) => setType(v as Epitope["type"] | "ALL")}
          >
            <TabsList>
              <TabsTrigger value="ALL">All ({epitopes.length})</TabsTrigger>
              <TabsTrigger value="CTL">
                CTL ({epitopes.filter((e) => e.type === "CTL").length})
              </TabsTrigger>
              <TabsTrigger value="HTL">
                HTL ({epitopes.filter((e) => e.type === "HTL").length})
              </TabsTrigger>
              <TabsTrigger value="BCELL_LINEAR">
                B-cell ({epitopes.filter((e) => e.type === "BCELL_LINEAR").length})
              </TabsTrigger>
            </TabsList>
          </Tabs>
          <div className="flex gap-1.5">
            <Badge className={cn(TYPE_BADGE.CTL.className)}>CTL</Badge>
            <Badge className={cn(TYPE_BADGE.HTL.className)}>HTL</Badge>
            <Badge className={cn(TYPE_BADGE.BCELL_LINEAR.className)}>B-cell</Badge>
          </div>
        </div>
      )}

      <DataTable
        columns={[
          "#",
          "Epitope Sequence",
          "Source Protein",
          "Position",
          "HLA Allele",
          "Method",
          "IC50 (nM)",
          "Percentile Rank",
          "Antigenicity",
          "Immunogenicity",
          "Toxic",
          "Allergenic",
          "IFN-γ",
          "IL-4",
          "IL-10",
        ]}
        rows={rows}
        filename={filename}
        title="Predicted & selected epitopes"
        searchPlaceholder="Search epitope sequence, allele…"
      />
    </div>
  );
}