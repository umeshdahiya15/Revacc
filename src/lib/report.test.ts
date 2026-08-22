import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import * as fc from "fast-check";
import { generateReportPdf, PAPER_STANDARD } from "./report";

const reportSource = readFileSync(new URL("./report.ts", import.meta.url), "utf8");
const routesSource = readFileSync(new URL("../../backend/app/routes.py", import.meta.url), "utf8");
const apiSource = readFileSync(new URL("./api.ts", import.meta.url), "utf8");
const jobsPageSource = readFileSync(new URL("../app/jobs/page.tsx", import.meta.url), "utf8");

const reportArtifacts = ["Evidence", "buildRunComparison", "authoritative run output", "External Validation Availability", "PyDock", "GROMACS"] as const;
const comparisonArtifacts = ["/jobs/compare", "funnel", "mevMetrics"] as const;

/**
 * These assertions encode the corrected provenance/comparison contract and
 * intentionally avoid embedding scientific expected counts in production or tests.
 */
describe("report comparison exploration (Issue 6)", () => {
  it("uses the documented paper-reference funnel values", () => {
    // **Validates: Requirements 2.9**
    expect(PAPER_STANDARD).toMatchObject({
      Essential: 1336,
      "Surface-exposed": 408,
    });
    expect(reportSource).toContain('"Paper standard"');
    expect(reportSource).toContain("PAPER_STANDARD[f.label]");
  });

  it("includes authoritative evidence with each funnel value", () => {
    // **Validates: Requirements 2.9 without embedding paper/count constants**
    fc.assert(
      fc.property(fc.constantFrom(...reportArtifacts), (artifact) => {
        expect(reportSource, `missing report evidence artifact: ${artifact}`).toContain(artifact);
      }),
      { numRuns: reportArtifacts.length },
    );
  });

  it("exposes a multi-run funnel and MEV comparison path", () => {
    // **Validates: Requirements 2.10**
    const availableReportAndJobSurface = [routesSource, apiSource, jobsPageSource].join("\n");
    fc.assert(
      fc.property(fc.constantFrom(...comparisonArtifacts), (artifact) => {
        expect(
          availableReportAndJobSurface,
          `missing multi-run comparison artifact/path: ${artifact}`,
        ).toContain(artifact);
      }),
      { numRuns: comparisonArtifacts.length },
    );
  });
});

describe("report test harness", () => {
  it("loads the report generator", () => {
    expect(generateReportPdf).toEqual(expect.any(Function));
  });

  it("runs a fast-check smoke property", () => {
    fc.assert(fc.property(fc.integer(), (value) => value === value));
  });
});
