import * as React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { OfficialLifecycleStatusPanel } from "@/components/pipeline/OfficialLifecycleStatusPanel";
import {
  officialLifecycleFromResult,
  publicJobForPresentation,
  redactOfficialLifecycleResult,
} from "@/lib/officialLifecycle";
import type { Job } from "@/types";

const fingerprint = "a".repeat(64);

function lifecycle(status: string, overrides: Record<string, unknown> = {}) {
  return {
    status,
    provider: "swissmodel",
    method: "automodel",
    requestId: "request-42",
    modelId: "model-7",
    modelUrl: "https://swissmodel.expasy.org/project/request-42/model/model-7/pdb/",
    mevFingerprint: fingerprint,
    submittedAt: "2025-01-02T03:04:05+00:00",
    updatedAt: "2025-01-02T03:05:05+00:00",
    ...overrides,
  };
}

function fixtureJob(result: Record<string, unknown>, errorMessage = "fixture error"): Job {
  return {
    id: "job-status-fixture",
    name: "Status fixture",
    pathogenName: "Fixture pathogen",
    status: "paused",
    currentPhase: 11,
    currentStep: 2,
    progress: 50,
    stepsCompleted: 20,
    totalSteps: 41,
    elapsedSeconds: 1,
    createdAt: "2025-01-01T00:00:00Z",
    updatedAt: "2025-01-01T00:00:01Z",
    config: {
      pathogenName: "Fixture pathogen",
      source: "pathogen",
      adjuvant: "ctxb",
      cdHitThreshold: 0.8,
      vaxijenThreshold: 0.5,
      expressionHost: "ecoli",
      expressionVector: "pet28a",
      runImmuneSim: true,
      runDisulfide: true,
      enableCoverage: true,
      hlaMhc1: [],
      hlaMhc2: [],
      bCellWindow: 7,
      coverageRegions: [],
    },
    phases: [{
      number: 11,
      name: "Structure",
      status: "paused",
      steps: [{
        id: "11-2",
        phase: 11,
        number: 2,
        name: "3D Structure Prediction",
        tool: "SWISS-MODEL",
        status: "paused",
        result,
        error: { message: errorMessage, retries: 0, severity: "pause", tool: "provider" },
      }],
    }],
  };
}

describe("official lifecycle UI presentation", () => {
  it.each([
    ["queued", "Queued"],
    ["running", "Running"],
    ["paused", "Paused"],
    ["failed", "Failed"],
    ["succeeded", "Succeeded"],
  ])("renders the %s status with safe lifecycle provenance", (status, label) => {
    // **Validates: Requirements 1.17, 2.18**
    const statusValue = officialLifecycleFromResult({ officialLifecycle: lifecycle(status) });
    expect(statusValue).not.toBeNull();

    const html = renderToStaticMarkup(<OfficialLifecycleStatusPanel lifecycle={statusValue!} />);
    expect(html).toContain("Official SWISS-MODEL status");
    expect(html).toContain(label);
    expect(html).toContain("Request ID:");
    expect(html).toContain("Model ID:");
    expect(html).toContain("Submitted:");
    expect(html).toContain("Open provider model");
  });

  it("redacts unallowlisted lifecycle bodies, headers, errors, and credential-bearing URLs", () => {
    // **Validates: Requirements 2.12, 3.14**
    const marker = "sensitive-fixture-marker";
    const rawResult = {
      officialLifecycle: lifecycle("failed", {
        messageCode: "swissmodel_provider_terminal_failure",
        message: marker,
        action: marker,
        modelUrl: "https://fixture-user:sensitive-fixture-marker@swissmodel.expasy.org/model.pdb",
        rawProviderPayload: { detail: marker },
        headers: { authorization: marker },
        stackTrace: marker,
      }),
      rawProviderPayload: marker,
      headers: { authorization: marker },
      requestBody: marker,
    };

    const statusValue = officialLifecycleFromResult(rawResult);
    const rawPresentation = redactOfficialLifecycleResult(rawResult);
    const html = renderToStaticMarkup(<OfficialLifecycleStatusPanel lifecycle={statusValue!} />);

    expect(statusValue?.modelUrl).toBeUndefined();
    expect(JSON.stringify(rawPresentation)).not.toContain(marker);
    expect(html).not.toContain(marker);
    expect(html).not.toContain("rawProviderPayload");
  });

  it("keeps legacy report/export jobs unchanged when no official status is present and sanitizes one when it is", () => {
    // **Validates: Requirements 3.13, 3.14**
    const legacy = fixtureJob({ sequence: "AVLG", provider: "user-provided" });
    expect(publicJobForPresentation(legacy)).toBe(legacy);

    const marker = "sensitive-fixture-marker";
    const official = fixtureJob({
      officialLifecycle: lifecycle("paused", { rawProviderPayload: marker }),
      rawProviderPayload: marker,
    }, marker);
    const projected = publicJobForPresentation(official);
    const projectedStep = projected.phases[0].steps[0];

    expect(projected).not.toBe(official);
    expect(JSON.stringify(projected)).not.toContain(marker);
    expect(projectedStep.result).toEqual({
      officialLifecycle: expect.objectContaining({ status: "paused", provider: "swissmodel" }),
    });
    expect(projectedStep.error?.message).not.toBe(marker);
  });
});
