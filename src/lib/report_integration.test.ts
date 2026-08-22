import { beforeEach, describe, expect, it, vi } from "vitest";
import type { Job, Step } from "@/types";

type TableCall = {
  head?: string[][];
  body?: string[][];
  startY?: number;
};

type PdfDocument = {
  lastAutoTable?: { finalY: number };
};

const { tableCalls } = vi.hoisted(() => ({ tableCalls: [] as TableCall[] }));

vi.mock("jspdf-autotable", () => ({
  default: (doc: PdfDocument, options: TableCall) => {
    tableCalls.push(options);
    doc.lastAutoTable = { finalY: Number(options.startY ?? 0) + 10 };
  },
}));

import { buildRunComparison, generateReportPdf } from "./report";

const step = (id: string, result?: Record<string, unknown>): Step => ({
  id,
  phase: Number(id.split("-")[0]),
  number: Number(id.split("-")[1]),
  name: id,
  tool: "fixture",
  status: "success",
  result,
});

const makeJob = (id: string, name: string, counts: { essential: number; surface: number }, mevLength: number): Job => ({
  id,
  name,
  pathogenName: "Streptococcus agalactiae",
  strain: "fixture",
  taxonId: 208435,
  status: "completed",
  currentPhase: 14,
  currentStep: 1,
  progress: 100,
  stepsCompleted: 41,
  totalSteps: 41,
  elapsedSeconds: 12,
  createdAt: "2025-01-01T00:00:00Z",
  updatedAt: "2025-01-01T00:00:12Z",
  config: {
    pathogenName: "Streptococcus agalactiae",
    strain: "fixture",
    taxonId: 208435,
    source: "pathogen",
    adjuvant: "ctxb",
    cdHitThreshold: 0.8,
    vaxijenThreshold: 0.5,
    expressionHost: "ecoli",
    expressionVector: "pet28a",
    runImmuneSim: true,
    runDisulfide: true,
    enableCoverage: true,
    hlaMhc1: ["HLA-A*02:01"],
    hlaMhc2: ["HLA-DRB1*01:01"],
    bCellWindow: 7,
    coverageRegions: [],
  },
  phases: [
    {
      number: 8,
      name: "Coverage",
      status: "completed",
      steps: [step("8-1", { coverage: [{ region: "Global", coverage: 75.5, hits: 2, count: 3 }] })],
    },
    {
      number: 9,
      name: "MEV",
      status: "completed",
      steps: [step("9-2", {
        sequence: "ACDEFGHIK",
        length: mevLength,
        mev_length: mevLength,
        adjuvant: "fixture adjuvant",
        linkers: "EAAAK/AAY/GPGPG/KK",
        method: "deterministic fixture assembly",
      })],
    },
    {
      number: 10,
      name: "Validation",
      status: "completed",
      steps: [step("10-1", { molecularWeight: 42.1, pi: 8.2 })],
    },
  ],
  funnel: [
    { key: "proteins", label: "Proteins curated", count: 100 },
    { key: "essential", label: "Essential", count: counts.essential },
    { key: "surface_exposed", label: "Surface-exposed", count: counts.surface },
  ],
  epitopes: [
    {
      id: `${id}-ctl`,
      type: "CTL",
      sequence: "ACDEFGHIK",
      sourceProtein: "FIX-INTEGRATION-0",
      predictionMethod: "IEDB NetMHCpan EL (MHC-I)",
      source: "real",
      selected: true,
    },
  ],
});

describe("pipeline report integration", () => {
  beforeEach(() => {
    tableCalls.length = 0;
  });

  it("renders paper-standard funnel values while retaining single-run report sections", () => {
    // **Validates: Requirements 2.9, 3.8**
    const job = makeJob("run-a", "Integration run A", { essential: 1330, surface: 405 }, 336);
    generateReportPdf(job);

    const funnelTable = tableCalls.find((call) => call.head?.[0]?.includes("Paper standard"));
    expect(funnelTable).toBeDefined();
    expect(funnelTable?.body).toEqual([
      ["Proteins curated", "100", "100", "—", "authoritative run output"],
      ["Essential", "1,330", "1330.0", "1,336", "authoritative run output"],
      ["Surface-exposed", "405", "405.0", "408", "authoritative run output"],
    ]);

    // The real generator reached each existing report section and emitted its
    // table; section headings remain produced by the production jsPDF writer.
    const tableHeads = tableCalls.map((call) => call.head?.[0]?.join(" | ") ?? "");
    expect(tableHeads).toEqual(expect.arrayContaining([
      "# | Sequence | Type | Source protein | Source ID | Position | Window | Allele | Method | Provenance | IC50 | Rank | Antigenicity | Sel.",
      "Step | Name | Tool | Status | Time | Key result",
      "Region / Population | Coverage (%) | Hits | Count",
      "Property | Value | Status",
      "Method | Requested protocol | Status | Provenance / reason",
    ]));
  });

  it("compares funnel counts and MEV metrics across two completed runs", () => {
    // **Validates: Requirements 2.10, 3.9**
    const first = makeJob("run-a", "Integration run A", { essential: 1330, surface: 405 }, 336);
    const second = makeJob("run-b", "Integration run B", { essential: 1342, surface: 412 }, 620);

    const comparison = buildRunComparison([first, second]);
    expect(comparison).toHaveLength(2);
    expect(comparison.map((run) => run.status)).toEqual(["completed", "completed"]);
    expect(comparison.map((run) => run.funnel.find((stage) => stage.label === "Essential")?.count)).toEqual([1330, 1342]);
    expect(comparison.map((run) => run.funnel.find((stage) => stage.label === "Surface-exposed")?.count)).toEqual([405, 412]);
    expect(comparison.map((run) => run.mevMetrics.mev_length)).toEqual([336, 620]);
    expect(comparison.map((run) => run.mevMetrics.length)).toEqual([336, 620]);
  });
});
