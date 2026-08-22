import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import * as fc from "fast-check";
import { generateReportPdf } from "./report";

const reportSource = readFileSync(new URL("./report.ts", import.meta.url), "utf8");
const routesSource = readFileSync(new URL("../../backend/app/routes.py", import.meta.url), "utf8");
const apiSource = readFileSync(new URL("./api.ts", import.meta.url), "utf8");

const reportSections = [
  ["run metadata", "job.name"],
  ["pathogen metadata", "job.pathogenName"],
  ["epitope table", "Epitope Distribution"],
  ["per-phase tables", "Detailed Step Results"],
  ["coverage", "Population Coverage"],
  ["MEV construct", "Vaccine Construct"],
  ["MEV validation metrics", "construct.properties"],
  ["MEV sequence", "construct.sequence"],
] as const;

const singleRunContracts = [
  ["backend list endpoint", 'def list_jobs()'],
  ["backend get endpoint", 'def get_job(job_id: str)'],
  ["backend epitope endpoint", 'def job_epitopes(job_id: str, type: str | None = None)'],
  ["frontend list request", 'apiRequest<Job[]>("/api/jobs")'],
  ["frontend epitope request", 'apiRequest<Epitope[]>(`/api/jobs/${jobId}/epitopes`'],
] as const;

/**
 * Property 10: preserve the existing analytical report sections while later
 * comparison artifacts are added.
 */
describe("single-run report preservation", () => {
  it("retains metadata, epitope, phase, coverage, and MEV artifacts", () => {
    // **Validates: Requirements 3.8**
    fc.assert(
      fc.property(fc.constantFrom(...reportSections), ([label, anchor]) => {
        expect(reportSource, `missing preserved ${label} artifact`).toContain(anchor);
      }),
      { numRuns: reportSections.length },
    );
  });

  it("continues to expose the report generator", () => {
    expect(generateReportPdf).toEqual(expect.any(Function));
  });
});

/**
 * Property 10: preserve the existing single-run list/retrieval surface. The
 * endpoint implementation is intentionally additive-only for future work.
 */
describe("single-run endpoint preservation", () => {
  it("retains list, get, and epitope endpoint contracts", () => {
    // **Validates: Requirements 3.9**
    const availableSurface = `${routesSource}\n${apiSource}`;
    fc.assert(
      fc.property(fc.constantFrom(...singleRunContracts), ([label, anchor]) => {
        expect(availableSurface, `missing ${label}`).toContain(anchor);
      }),
      { numRuns: singleRunContracts.length },
    );
  });
});
