/**
 * Revacc report generation — builds real, self-contained exports from a
 * live Job record:
 *
 *  - `generateReportPdf`  analytical PDF (funnel chart, pie charts, phase
 *                         durations, per-phase step tables, coverage,
 *                         construct section)
 *  - `generateRawPdf`     complete raw dump of EVERY step's result/error as
 *                         paginated monospaced JSON
 *  - `exportRawJson`      full job record as JSON
 *  - `exportZip`          real ZIP archive bundling every export
 */

import { jsPDF } from "jspdf";
import autoTable from "jspdf-autotable";
import { downloadFile, toCsv, formatDuration } from "@/lib/utils";
import type { Job, Step } from "@/types";

export type ExportFormat =
  | "zip"
  | "report"
  | "rawpdf"
  | "rawjson"
  | "csv"
  | "fasta"
  | "genbank";

// ---------------------------------------------------------------------------
// Shared helpers
// ---------------------------------------------------------------------------

const STATUS_COLORS: Record<string, [number, number, number]> = {
  success: [16, 185, 129],
  skipped: [100, 116, 139],
  failed: [239, 68, 68],
  paused: [245, 158, 11],
  running: [59, 130, 246],
  pending: [203, 213, 225],
  completed: [16, 185, 129],
};

function allSteps(job: Job): Step[] {
  return job.phases.flatMap((p) => p.steps);
}

function stepById(job: Job, id: string): Step | undefined {
  return allSteps(job).find((s) => s.id === id);
}

function isObj(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

function titleize(s: string): string {
  return s
    .replace(/[_-]+/g, " ")
    .replace(/([a-z])([A-Z])/g, "$1 $2")
    .replace(/\b\w/g, (m) => m.toUpperCase());
}

// ---------------------------------------------------------------------------
// Report PDF
// ---------------------------------------------------------------------------

export function generateReportPdf(job: Job): void {
  const doc = new jsPDF({ unit: "mm", format: "a4" });
  const W = doc.internal.pageSize.getWidth();
  const M = 14;
  let y = 0;

  const header = (title: string) => {
    if (y > 270) {
      doc.addPage();
      y = 16;
    }
    doc.setFont("helvetica", "bold");
    doc.setFontSize(13);
    doc.setTextColor(15, 23, 42);
    doc.text(title, M, y);
    y += 6;
    doc.setDrawColor(226, 232, 240);
    doc.line(M, y - 2, W - M, y - 2);
    y += 3;
  };

  // ---- Cover ----
  doc.setFillColor(37, 99, 235);
  doc.rect(0, 0, W, 34, "F");
  doc.setTextColor(255, 255, 255);
  doc.setFont("helvetica", "bold");
  doc.setFontSize(20);
  doc.text("Revacc", M, 15);
  doc.setFontSize(10);
  doc.setFont("helvetica", "normal");
  doc.text("Reverse-Vaccinology Pipeline Report", M, 23);
  doc.setFontSize(8);
  doc.text(`Generated ${new Date().toLocaleString()}`, M, 29);
  doc.setTextColor(15, 23, 42);
  y = 46;

  doc.setFont("helvetica", "bold");
  doc.setFontSize(16);
  doc.text(job.name, M, y);
  y += 7;
  doc.setFont("helvetica", "normal");
  doc.setFontSize(10);
  doc.setTextColor(71, 85, 105);
  doc.text(
    `${job.pathogenName}${job.strain ? ` · ${job.strain}` : ""}${job.taxonId ? ` · txid ${job.taxonId}` : ""}`,
    M,
    y,
  );
  y += 8;

  // Summary stat cards
  const steps = allSteps(job);
  const counts = {
    success: steps.filter((s) => s.status === "success").length,
    skipped: steps.filter((s) => s.status === "skipped").length,
    failed: steps.filter((s) => s.status === "failed").length,
    pending: steps.filter((s) => s.status === "pending" || s.status === "running").length,
  };
  const cards: [string, string][] = [
    ["Status", titleize(job.status)],
    ["Progress", `${Math.round(job.progress)}%`],
    ["Steps done", `${counts.success + counts.skipped} / ${steps.length}`],
    ["Epitopes", String(job.epitopes?.length ?? 0)],
    ["Elapsed", formatDuration(job.elapsedSeconds)],
    ["Phases", String(job.phases.length)],
  ];
  const cw = (W - M * 2 - 10) / 6;
  cards.forEach(([label, value], i) => {
    const x = M + i * (cw + 2);
    doc.setFillColor(248, 250, 252);
    doc.roundedRect(x, y, cw, 16, 2, 2, "F");
    doc.setFontSize(7);
    doc.setTextColor(100, 116, 139);
    doc.text(label.toUpperCase(), x + 3, y + 5.5);
    doc.setFontSize(11);
    doc.setFont("helvetica", "bold");
    doc.setTextColor(15, 23, 42);
    doc.text(value, x + 3, y + 12);
    doc.setFont("helvetica", "normal");
  });
  y += 24;

  // ---- Filtering funnel (table format) ----
  const funnel = funnelData(job);
  if (funnel.length > 1) {
    header("Proteome Filtering Funnel");
    autoTable(doc, {
      startY: y,
      margin: { left: M, right: M },
      headStyles: { fillColor: [37, 99, 235], fontSize: 7.5 },
      styles: { fontSize: 7.5, cellPadding: 1.6 },
      head: [["Filter Step", "Count", "Retention (%)"]],
      body: funnel.map((f, i) => [
        f.label,
        f.count.toLocaleString(),
        i === 0 ? "100" : ((f.count / funnel[0].count) * 100).toFixed(1),
      ]),
    });
    y = (doc as unknown as { lastAutoTable: { finalY: number } }).lastAutoTable.finalY + 8;
  }

  // ---- Epitope table ----
  const eps = job.epitopes ?? [];
  if (eps.length > 0) {
    header("Epitope Distribution");
    const top = [...eps]
      .sort((a, b) => (b.antigenicityScore ?? 0) - (a.antigenicityScore ?? 0))
      .slice(0, 15);
    autoTable(doc, {
      startY: y,
      margin: { left: M, right: M },
      headStyles: { fillColor: [37, 99, 235], fontSize: 7.5 },
      styles: { fontSize: 7, cellPadding: 1.5 },
      head: [["#", "Sequence", "Type", "Source protein", "Allele", "Method", "IC50", "Rank", "Antigenicity", "Sel."]],
      body: top.map((e, i) => [
        String(i + 1),
        e.sequence,
        e.type,
        e.sourceProtein.slice(0, 30),
        e.hlaAllele ?? "—",
        e.predictionMethod ?? "—",
        e.ic50 != null ? e.ic50.toFixed(1) : "—",
        e.percentileRank != null ? e.percentileRank.toFixed(2) : "—",
        e.antigenicityScore != null ? e.antigenicityScore.toFixed(2) : "—",
        e.selected ? "Yes" : "No",
      ]),
    });
    y = (doc as unknown as { lastAutoTable: { finalY: number } }).lastAutoTable.finalY + 8;
  }

  // ---- Per-phase step tables ----
  header("Detailed Step Results");
  job.phases.forEach((phase) => {
    if (y > 250) {
      doc.addPage();
      y = 16;
    }
    doc.setFont("helvetica", "bold");
    doc.setFontSize(10);
    doc.setTextColor(37, 99, 235);
    doc.text(`Phase ${phase.number} — ${phase.name}`, M, y);
    y += 3;
    autoTable(doc, {
      startY: y,
      margin: { left: M, right: M },
      headStyles: { fillColor: [71, 85, 105], fontSize: 7 },
      styles: { fontSize: 6.6, cellPadding: 1.4 },
      head: [["Step", "Name", "Tool", "Status", "Time", "Key result"]],
      body: phase.steps.map((s) => [
        `${s.phase}.${s.number}`,
        s.name,
        s.tool,
        titleize(s.status),
        s.duration != null ? `${s.duration}s` : "—",
        keySummary(s),
      ]),
      didParseCell: (data) => {
        if (data.section === "body" && data.column.index === 3) {
          const c = STATUS_COLORS[String(data.cell.raw).toLowerCase()];
          if (c) data.cell.styles.textColor = c;
        }
      },
    });
    y = (doc as unknown as { lastAutoTable: { finalY: number } }).lastAutoTable.finalY + 7;
  });

  // ---- Population coverage ----
  const cov = coverageRows(job);
  if (cov.length > 0) {
    header("Population Coverage");
    autoTable(doc, {
      startY: y,
      margin: { left: M, right: M },
      headStyles: { fillColor: [37, 99, 235], fontSize: 7.5 },
      styles: { fontSize: 7.5, cellPadding: 1.6 },
      head: [["Region / Population", "Coverage (%)", "Hits", "Count"]],
      body: cov.map((r) => [r.name, r.coverage, r.hits, r.count]),
    });
    y = (doc as unknown as { lastAutoTable: { finalY: number } }).lastAutoTable.finalY + 8;
  }

  // ---- MEV construct ----
  const construct = constructInfo(job);
  if (construct) {
    if (y > 220) {
      doc.addPage();
      y = 16;
    }
    header("Vaccine Construct");
    doc.setFontSize(9);
    doc.setFont("helvetica", "normal");
    doc.setTextColor(30, 41, 59);
    const lines = [
      `Construct length: ${construct.length} aa`,
      `Adjuvant: ${construct.adjuvant}`,
      construct.linkers ? `Linkers: ${construct.linkers}` : null,
      construct.method ? `Assembly: ${construct.method}` : null,
    ].filter(Boolean) as string[];
    lines.forEach((l, i) => doc.text(l, M, y + i * 5));
    y += lines.length * 5 + 3;

    const props = construct.properties;
    if (props.length > 0) {
      autoTable(doc, {
        startY: y,
        margin: { left: M, right: M },
        headStyles: { fillColor: [37, 99, 235], fontSize: 7.5 },
        styles: { fontSize: 7.5, cellPadding: 1.6 },
        head: [["Property", "Value", "Status"]],
        body: props,
      });
      y = (doc as unknown as { lastAutoTable: { finalY: number } }).lastAutoTable.finalY + 6;
    }

    if (construct.sequence) {
      if (y > 230) {
        doc.addPage();
        y = 16;
      }
      doc.setFont("helvetica", "bold");
      doc.setFontSize(10);
      doc.text("Construct sequence", M, y);
      y += 5;
      doc.setFont("courier", "normal");
      doc.setFontSize(7);
      const wrapped = doc.splitTextToSize(
        (construct.sequence.match(/.{1,60}/g) ?? [construct.sequence]).join(" "),
        W - M * 2,
      );
      wrapped.forEach((line: string) => {
        if (y > 280) {
          doc.addPage();
          y = 16;
        }
        doc.text(line, M, y);
        y += 3.2;
      });
      y += 4;
    }
  }

  // ---- Footer ----
  const pages = doc.getNumberOfPages();
  for (let p = 1; p <= pages; p++) {
    doc.setPage(p);
    doc.setFontSize(7);
    doc.setTextColor(148, 163, 184);
    doc.text(`Revacc · Job ${job.id} · ${job.pathogenName}`, M, 290);
    doc.text(`Page ${p} / ${pages}`, W - M, 290, { align: "right" });
  }

  doc.save(`${slug(job)}_report.pdf`);
}

// ---------------------------------------------------------------------------
// Raw PDF — every step, complete JSON
// ---------------------------------------------------------------------------

export function generateRawPdf(job: Job): void {
  const doc = new jsPDF({ unit: "mm", format: "a4" });
  const W = doc.internal.pageSize.getWidth();
  const M = 12;
  let y = 0;

  // Cover / index
  doc.setFillColor(15, 23, 42);
  doc.rect(0, 0, W, 30, "F");
  doc.setTextColor(255, 255, 255);
  doc.setFont("helvetica", "bold");
  doc.setFontSize(16);
  doc.text("Revacc — Complete Raw Data Dump", M, 13);
  doc.setFont("helvetica", "normal");
  doc.setFontSize(9);
  doc.text(
    `${job.name} · ${job.pathogenName} · job ${job.id} · ${new Date().toLocaleString()}`,
    M,
    21,
  );
  doc.setTextColor(15, 23, 42);
  y = 40;

  const steps = allSteps(job);
  doc.setFontSize(9);
  doc.text(
    `This document contains the complete raw output of all ${steps.length} pipeline steps, plus the full epitope record (${job.epitopes?.length ?? 0} entries).`,
    M,
    y,
    { maxWidth: W - M * 2 },
  );
  y += 10;

  const write = (text: string, font: "courier" | "helvetica" = "courier", size = 6.4) => {
    doc.setFont(font, "normal");
    doc.setFontSize(size);
    const lines = doc.splitTextToSize(text, W - M * 2) as string[];
    lines.forEach((line) => {
      if (y > 285) {
        doc.addPage();
        y = 14;
      }
      doc.text(line, M, y);
      y += size * 0.42 + 0.5;
    });
  };

  const sectionTitle = (t: string) => {
    if (y > 265) {
      doc.addPage();
      y = 14;
    }
    doc.setFont("helvetica", "bold");
    doc.setFontSize(10);
    doc.setTextColor(37, 99, 235);
    doc.text(t, M, y);
    y += 2;
    doc.setDrawColor(226, 232, 240);
    doc.line(M, y, W - M, y);
    y += 4.5;
    doc.setTextColor(15, 23, 42);
  };

  // Job meta
  sectionTitle("Job metadata");
  write(
    JSON.stringify(
      {
        id: job.id,
        name: job.name,
        pathogenName: job.pathogenName,
        strain: job.strain,
        taxonId: job.taxonId,
        status: job.status,
        config: job.config,
        funnel: job.funnel,
        createdAt: job.createdAt,
        updatedAt: job.updatedAt,
        elapsedSeconds: job.elapsedSeconds,
      },
      null,
      2,
    ),
  );
  y += 4;

  // Every step
  steps.forEach((s) => {
    sectionTitle(`Step ${s.phase}.${s.number} — ${s.name}  (${s.tool} · ${s.status})`);
    write(
      JSON.stringify(
        {
          id: s.id,
          status: s.status,
          percent: s.percent,
          duration: s.duration,
          startedAt: s.startedAt,
          completedAt: s.completedAt,
          error: s.error ?? null,
          result: s.result ?? null,
        },
        null,
        2,
      ),
    );
    y += 4;
  });

  // Epitopes — compact one per line for density
  const eps = job.epitopes ?? [];
  if (eps.length > 0) {
    sectionTitle(`Epitopes (${eps.length} entries, one JSON per line)`);
    eps.forEach((e) => {
      if (y > 285) {
        doc.addPage();
        y = 14;
      }
      write(JSON.stringify(e));
    });
    y += 4;
  }

  const pages = doc.getNumberOfPages();
  for (let p = 1; p <= pages; p++) {
    doc.setPage(p);
    doc.setFontSize(7);
    doc.setTextColor(148, 163, 184);
    doc.text(`Revacc raw dump · Job ${job.id}`, M, 290);
    doc.text(`Page ${p} / ${pages}`, W - M, 290, { align: "right" });
  }

  doc.save(`${slug(job)}_raw_data.pdf`);
}

// ---------------------------------------------------------------------------
// Raw JSON + ZIP bundle
// ---------------------------------------------------------------------------

export function exportRawJson(job: Job): void {
  downloadFile(
    JSON.stringify(job, null, 2),
    `${slug(job)}_raw.json`,
    "application/json",
  );
}

export function exportZip(job: Job): void {
  const eps = job.epitopes ?? [];
  const files: { name: string; content: string }[] = [
    {
      name: "README.txt",
      content: [
        `Revacc export bundle`,
        `Job: ${job.name} (${job.id})`,
        `Pathogen: ${job.pathogenName}${job.strain ? ` · ${job.strain}` : ""}`,
        `Status: ${job.status} · ${job.progress}% complete`,
        `Generated: ${new Date().toISOString()}`,
        ``,
        `Contents:`,
        `  epitopes.csv        — all predicted epitopes`,
        `  construct.fasta     — vaccine construct sequence`,
        `  construct.gb        — GenBank record`,
        `  raw.json            — complete job record incl. every step result`,
        `  funnel.json         — filtering funnel counts`,
        `  report.txt          — plain-text phase summary`,
      ].join("\n"),
    },
  ];

  if (eps.length > 0) {
    files.push({
      name: "epitopes.csv",
      content: toCsv(
        eps.map((e, i) => ({
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
          Selected: e.selected ? "Yes" : "No",
        })),
      ),
    });
  }

  const construct = constructInfo(job);
  if (construct?.sequence) {
    files.push({
      name: "construct.fasta",
      content: `>${job.name.replace(/\s+/g, "_")}_MEV_${construct.length}aa\n${(construct.sequence.match(/.{1,60}/g) ?? [construct.sequence]).join("\n")}\n`,
    });
    files.push({
      name: "construct.gb",
      content: [
        `LOCUS       ${slug(job).toUpperCase()}  ${construct.length} aa  linear  VRL ${new Date().toISOString().slice(0, 10)}`,
        `DEFINITION  Multi-epitope vaccine construct`,
        `FEATURES             Location/Qualifiers`,
        `     source          1..${construct.length}`,
        `                     /organism="Synthetic construct"`,
        `ORIGIN`,
        ...(construct.sequence.match(/.{1,60}/g) ?? [construct.sequence]),
        "//",
      ].join("\n"),
    });
  }

  files.push({ name: "raw.json", content: JSON.stringify(job, null, 2) });
  files.push({ name: "funnel.json", content: JSON.stringify(funnelData(job), null, 2) });
  files.push({
    name: "report.txt",
    content: job.phases
      .map(
        (p) =>
          `Phase ${p.number}: ${p.name} [${p.status}]\n` +
          p.steps
            .map((s) => `  ${s.phase}.${s.number} ${s.name} (${s.tool}) — ${s.status}${s.duration != null ? ` · ${s.duration}s` : ""}`)
            .join("\n"),
      )
      .join("\n\n"),
  });

  downloadFile(zip(files), `${slug(job)}_export.zip`, "application/zip");
}

// ---------------------------------------------------------------------------
// Minimal ZIP writer (store method, no compression, CRC-32)
// ---------------------------------------------------------------------------

const CRC_TABLE = (() => {
  const t = new Uint32Array(256);
  for (let n = 0; n < 256; n++) {
    let c = n;
    for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    t[n] = c >>> 0;
  }
  return t;
})();

function crc32(bytes: Uint8Array): number {
  let c = 0xffffffff;
  for (let i = 0; i < bytes.length; i++) c = CRC_TABLE[(c ^ bytes[i]) & 0xff] ^ (c >>> 8);
  return (c ^ 0xffffffff) >>> 0;
}

function zip(files: { name: string; content: string }[]): Uint8Array {
  const enc = new TextEncoder();
  const chunks: Uint8Array[] = [];
  const central: Uint8Array[] = [];
  let offset = 0;

  const u16 = (v: number) => new Uint8Array([v & 0xff, (v >> 8) & 0xff]);
  const u32 = (v: number) =>
    new Uint8Array([v & 0xff, (v >> 8) & 0xff, (v >> 16) & 0xff, (v >>> 24) & 0xff]);
  const concat = (arrs: Uint8Array[]) => {
    const len = arrs.reduce((a, b) => a + b.length, 0);
    const out = new Uint8Array(len);
    let p = 0;
    arrs.forEach((a) => {
      out.set(a, p);
      p += a.length;
    });
    return out;
  };

  files.forEach((f) => {
    const nameB = enc.encode(f.name);
    const dataB = enc.encode(f.content);
    const crc = crc32(dataB);

    const local = concat([
      u32(0x04034b50),
      u16(20),
      u16(0x0800),
      u16(0),
      u16(0),
      u16(0),
      u32(crc),
      u32(dataB.length),
      u32(dataB.length),
      u16(nameB.length),
      u16(0),
      nameB,
      dataB,
    ]);
    chunks.push(local);

    central.push(
      concat([
        u32(0x02014b50),
        u16(20),
        u16(20),
        u16(0x0800),
        u16(0),
        u16(0),
        u16(0),
        u32(crc),
        u32(dataB.length),
        u32(dataB.length),
        u16(nameB.length),
        u16(0),
        u16(0),
        u16(0),
        u16(0),
        u32(0),
        u32(offset),
        nameB,
      ]),
    );
    offset += local.length;
  });

  const centralBuf = concat(central);
  const end = concat([
    u32(0x06054b50),
    u16(0),
    u16(0),
    u16(files.length),
    u16(files.length),
    u32(centralBuf.length),
    u32(offset),
    u16(0),
  ]);

  const all = concat([...chunks, centralBuf, end]);
  return all;
}

// ---------------------------------------------------------------------------
// Data extraction helpers
// ---------------------------------------------------------------------------

function slug(job: Job): string {
  return (job.name || job.id).replace(/[^A-Za-z0-9]/g, "_").toLowerCase() || job.id;
}

function num(v: unknown): number | null {
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

function funnelData(job: Job): { label: string; count: number }[] {
  if (job.funnel && job.funnel.length > 0) {
    return job.funnel.map((f) => ({ label: f.label, count: f.count }));
  }
  const out: { label: string; count: number }[] = [];
  const r11 = stepById(job, "1-1")?.result;
  if (isObj(r11)) {
    out.push({ label: "Retrieved proteome", count: num(r11.proteins) ?? num(r11.total) ?? 0 });
  }
  const r12 = stepById(job, "1-2")?.result;
  if (isObj(r12)) out.push({ label: "Non-redundant", count: num(r12.nonRedundant) ?? 0 });
  const r21 = stepById(job, "2-1")?.result;
  if (isObj(r21)) out.push({ label: "Essential", count: num(r21.essential) ?? 0 });
  const r24 = stepById(job, "2-4")?.result;
  if (isObj(r24)) out.push({ label: "Surface-exposed", count: num(r24.surface_exposed_count) ?? 0 });
  const r31 = stepById(job, "3-1")?.result;
  if (isObj(r31)) {
    out.push({
      label: "Non-allergenic",
      count: num(r31.non_allergen_count) ?? num(r31.total_analyzed) ?? 0,
    });
  }
  const r32 = stepById(job, "3-2")?.result;
  if (isObj(r32)) out.push({ label: "Antigenic", count: num(r32.antigenic_count) ?? 0 });
  const r34 = stepById(job, "3-4")?.result;
  if (isObj(r34)) out.push({ label: "Non-human homolog", count: num(r34.non_homologous_count) ?? 0 });
  return out.filter((f) => f.count > 0);
}

function coverageRows(job: Job): { name: string; coverage: string; hits: string; count: string }[] {
  const r = stepById(job, "8-1")?.result;
  if (!isObj(r)) return [];
  const rows: { name: string; coverage: string; hits: string; count: string }[] = [];
  if (Array.isArray(r.coverage)) {
    (r.coverage as Record<string, unknown>[]).forEach((row) => {
      rows.push({
        name: String(row.region ?? row.population ?? "—"),
        coverage: row.coverage != null ? Number(row.coverage).toFixed(2) : "—",
        hits: String(row.hit ?? row.hits ?? "—"),
        count: String(row.sample_size ?? row.count ?? "—"),
      });
    });
  }
  if (rows.length === 0 && typeof r.global === "number") {
    rows.push({ name: "Global (all populations)", coverage: r.global.toFixed(2), hits: "—", count: "—" });
  }
  Object.entries(r).forEach(([k, v]) => {
    if (/coverage/i.test(k) && isObj(v)) {
      Object.entries(v).forEach(([pop, cv]) => {
        if (typeof cv === "number") {
          rows.push({ name: titleize(pop), coverage: cv.toFixed(2), hits: "—", count: "—" });
        }
      });
    }
  });
  return rows;
}

function constructInfo(job: Job): {
  length: number;
  adjuvant: string;
  linkers?: string;
  method?: string;
  sequence?: string;
  properties: string[][];
} | null {
  const r92 = stepById(job, "9-2")?.result ?? stepById(job, "9-1")?.result;
  if (!isObj(r92)) return null;
  const seq = typeof r92.sequence === "string" ? r92.sequence : undefined;
  const length = num(r92.length) ?? seq?.length ?? 0;
  if (length === 0 && !seq) return null;
  const props: string[][] = [];
  const push = (k: string, v: unknown) => {
    if (v !== undefined && v !== null) props.push([titleize(k), String(v), "info"]);
  };
  ["molecularWeight", "pi", "instabilityIndex", "gravy", "aliphaticIndex", "solubility", "hydrophobicity"].forEach(
    (k) => push(k, (r92 as Record<string, unknown>)[k]),
  );
  const r10 = stepById(job, "10-1")?.result ?? stepById(job, "10-2")?.result;
  if (isObj(r10)) {
    Object.entries(r10).forEach(([k, v]) => {
      if (typeof v === "number" || typeof v === "string") {
        props.push([titleize(k), String(v), /fail|low|poor/i.test(k + String(v)) ? "warn" : "pass"]);
      }
    });
  }
  return {
    length,
    adjuvant: String((job.config as unknown as Record<string, unknown> | undefined)?.adjuvant ?? "—"),
    linkers: typeof r92.linkers === "string" ? r92.linkers : undefined,
    method: typeof r92.method === "string" ? r92.method : undefined,
    sequence: seq,
    properties: props.slice(0, 14),
  };
}

function keySummary(step: Step): string {
  if (step.status === "failed") return step.error?.message?.slice(0, 60) ?? "failed";
  if (step.status === "skipped") return "skipped";
  const r = step.result;
  if (!isObj(r)) return "—";
  const picks: [string, string][] = [];
  const prefer = [
    "proteins",
    "nonRedundant",
    "essential",
    "surface_exposed_count",
    "transmembrane_count",
    "allergen_count",
    "antigenic_count",
    "virulence_count",
    "non_homologous_count",
    "epitopes",
    "selected",
    "count",
    "global",
    "length",
    "coverage",
  ];
  for (const key of prefer) {
    if (picks.length >= 3) break;
    const v = r[key];
    if (typeof v === "number") picks.push([titleize(key), v.toLocaleString()]);
  }
  if (picks.length === 0) {
    const first = Object.entries(r).find(
      (entry): entry is [string, number] => typeof entry[1] === "number",
    );
    if (first) picks.push([titleize(first[0]), first[1].toLocaleString()]);
  }
  return picks.length ? picks.map(([k, v]) => `${k}: ${v}`).join(" · ") : "—";
}
