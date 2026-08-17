"use client";

import { useCallback } from "react";
import { downloadFile, toCsv } from "@/lib/utils";
import {
  exportRawJson,
  exportZip,
  generateRawPdf,
  generateReportPdf,
  type ExportFormat,
} from "@/lib/report";
import type { Job } from "@/types";

export type { ExportFormat };

function constructStep(job: Job) {
  const steps = job.phases.flatMap((p) => p.steps);
  // 9-2 (MEV Assembly) carries the assembled sequence; 9-1 only picks the
  // adjuvant, so it must never be picked first.
  return steps.find((s) => s.id === "9-2") ?? steps.find((s) => s.id === "9-1");
}

/**
 * Client-side export dispatcher. Every payload is assembled from the real
 * `Job` record returned by GET /api/jobs — no mock data.
 */
export function useExport() {
  const exportJob = useCallback((job: Job, format: ExportFormat) => {
    const slug = job.name.replace(/[^A-Za-z0-9]/g, "_").toLowerCase() || job.id;

    switch (format) {
      case "report":
        generateReportPdf(job);
        return;
      case "rawpdf":
        generateRawPdf(job);
        return;
      case "rawjson":
        exportRawJson(job);
        return;
      case "zip":
        exportZip(job);
        return;
      case "csv": {
        const rows = (job.epitopes ?? []).map((e, i) => ({
          "#": i + 1,
          Type: e.type,
          Sequence: e.sequence,
          "Source Protein": e.sourceProteinName ?? e.sourceProtein,
          "Position": e.startPosition ?? "",
          "HLA Allele": e.hlaAllele ?? "",
          "Method": e.predictionMethod ?? "",
          "IC50 (nM)": e.ic50 ?? "",
          "Percentile Rank": e.percentileRank ?? "",
          Antigenicity: e.antigenicityScore ?? "",
          Immunogenicity: e.immunogenicityScore ?? "",
          Toxic: e.isToxic ? "Yes" : "No",
          Allergenic: e.isAllergenic ? "Yes" : "No",
        }));
        downloadFile(toCsv(rows), `${slug}_epitopes.csv`, "text/csv");
        return;
      }
      case "fasta": {
        const r = constructStep(job)?.result as Record<string, unknown> | undefined;
        const seq = typeof r?.sequence === "string" ? r.sequence : "";
        const len = typeof r?.length === "number" ? r.length : seq.length;
        const fasta =
          seq && seq.length
            ? `>${job.name}_MEV_${len}aa\n${seq.match(/.{1,60}/g)?.join("\n")}\n`
            : `>${job.name}_MEV_construct\n(no assembled sequence recorded yet)\n`;
        downloadFile(fasta, `${slug}_mev_construct.fasta`, "text/plain");
        return;
      }
      case "genbank": {
        const r = constructStep(job)?.result as Record<string, unknown> | undefined;
        const seq = typeof r?.sequence === "string" ? r.sequence : "";
        const len = typeof r?.length === "number" ? r.length : seq.length;
        const record = [
          `LOCUS       ${slug.toUpperCase()}  ${len} aa  linear  VRL ${new Date().toISOString().slice(0, 10)}`,
          "DEFINITION  Multi-epitope vaccine (assembled construct)",
          "FEATURES             Location/Qualifiers",
          `     source          1..${len}`,
          '                     /organism="Synthetic construct"',
          `     CDS             1..${len}`,
          '                     /product="MEV polyepitope vaccine"',
          "ORIGIN",
          seq || "(no sequence)",
          "//",
        ].join("\n");
        downloadFile(record, `${slug}_cloning.gb`, "text/plain");
        return;
      }
    }
  }, []);

  return { exportJob };
}
