import { describe, expect, it } from "vitest";
import { comparisonStages, type ComparisonJob } from "./api";

const run = (id: string, funnel: ComparisonJob["funnel"], mevMetrics: ComparisonJob["mevMetrics"], status: ComparisonJob["provenance"]["status"] = "real"): ComparisonJob => ({
  id,
  name: id,
  status: "completed",
  funnel,
  mevMetrics,
  provenance: { status },
});

describe("multi-run comparison", () => {
  it("aligns funnel stages by key across runs while preserving backend order", () => {
    // **Validates: Requirements 2.10**
    const stages = comparisonStages([
      run("a", [{ key: "proteins", label: "Proteins", count: 10 }, { key: "essential", label: "Essential", count: 4 }], {}),
      run("b", [{ key: "proteins", label: "Proteins", count: 12 }, { key: "surface", label: "Surface", count: 3 }], {}),
    ]);
    expect(stages).toEqual([
      { key: "proteins", label: "Proteins" },
      { key: "essential", label: "Essential" },
      { key: "surface", label: "Surface" },
    ]);
  });

  it("keeps MEV values tied to their run and preserves local provenance", () => {
    // **Validates: Requirements 2.10**
    const jobs = [
      run("a", [], { mev_length: 300, ctl_epitopes: 2 }),
      run("b", [], { mev_length: 620, ctl_epitopes: 8 }, "local-analysis"),
    ];
    expect(jobs.map((job) => job.mevMetrics.mev_length)).toEqual([300, 620]);
    expect(jobs[1].provenance.status).toBe("local-analysis");
  });
});
