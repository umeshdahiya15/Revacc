import type { Job, OfficialLifecycleState, OfficialLifecycleStatus } from "@/types";

const LIFECYCLE_STATES = new Set<OfficialLifecycleState>([
  "queued",
  "running",
  "paused",
  "failed",
  "succeeded",
]);

const STATE_COPY: Record<OfficialLifecycleState, Pick<OfficialLifecycleStatus, "message" | "action">> = {
  queued: {
    message: "The official SWISS-MODEL job is queued for provider processing.",
    action: "Refresh this run to see provider progress.",
  },
  running: {
    message: "The official SWISS-MODEL job is being processed by the provider.",
    action: "Refresh this run to see provider progress.",
  },
  paused: {
    message: "The official SWISS-MODEL job is paused and needs review.",
    action: "Review the status or attach a validated external PDB model.",
  },
  failed: {
    message: "The official SWISS-MODEL job ended without a validated model.",
    action: "Review the status or attach a validated external PDB model before retrying.",
  },
  succeeded: {
    message: "The official SWISS-MODEL job completed with validated lifecycle status.",
    action: "Review the qualified structure result before continuing downstream analysis.",
  },
};

const MESSAGE_COPY: Record<string, Pick<OfficialLifecycleStatus, "message" | "action">> = {
  swissmodel_official_lifecycle_unavailable: {
    message: "The official lifecycle is not available for this exact MEV.",
    action: "Attach a validated external PDB model or retry when official lifecycle support is available.",
  },
  swissmodel_create_outcome_unknown: {
    message: "The provider could not confirm whether the official job was accepted.",
    action: "Do not resubmit automatically; verify the provider job or attach a validated external model.",
  },
  swissmodel_create_request_id_missing: {
    message: "The official job could not be recorded safely.",
    action: "Verify the provider job manually before retrying, or attach a validated external model.",
  },
  swissmodel_lifecycle_rate_limited: {
    message: "The official lifecycle remained rate-limited after its retry budget.",
    action: "Wait for the provider retry interval, then resume the recorded job.",
  },
  swissmodel_lifecycle_timeout: {
    message: "The official lifecycle timed out within its bounded deadline.",
    action: "Resume the recorded job later without creating a duplicate submission.",
  },
  swissmodel_lifecycle_retry_exhausted: {
    message: "The official lifecycle remained unavailable after its retry budget.",
    action: "Resume the recorded job later or attach a validated external model.",
  },
  swissmodel_lifecycle_record_invalid: {
    message: "The recorded official lifecycle metadata cannot be used safely.",
    action: "Verify the provider job or attach a validated external model instead of resubmitting automatically.",
  },
  swissmodel_submission_not_admitted: {
    message: "Official SWISS-MODEL submission is not available for the current MEV.",
    action: "Review the official submission prerequisites and retry Step 11-2.",
  },
  swissmodel_polling_unavailable: {
    message: "The official lifecycle status cannot be refreshed safely.",
    action: "Keep the job paused and use a validated external model until lifecycle support is available.",
  },
  swissmodel_model_result_unavailable: {
    message: "A provider-approved model result is not available for this job.",
    action: "Attach a validated external PDB model until a documented result path is available.",
  },
  swissmodel_pdb_qualification_required: {
    message: "The provider model needs guarded exact-PDB qualification before it can be used.",
    action: "Attach a validated external PDB model or configure the guarded qualification path.",
  },
  swissmodel_pdb_qualification_failed: {
    message: "The provider model did not pass guarded exact-MEV qualification.",
    action: "Use a complete exact PDB model or retry the recorded provider job.",
  },
  swissmodel_cancellation_unavailable: {
    message: "The official job cannot be cancelled through the documented lifecycle.",
    action: "Keep the job paused and contact the provider manually if cancellation is required.",
  },
  swissmodel_cancellation_not_available: {
    message: "Cancellation is available only for a recorded queued or running job.",
    action: "Select a recorded non-terminal official job before requesting cancellation.",
  },
  swissmodel_provider_terminal_failure: {
    message: "The provider reported a terminal model failure.",
    action: "Review the provider job and attach a validated external model before retrying Step 11-2.",
  },
  swissmodel_lifecycle_transition_invalid: {
    message: "The requested official lifecycle transition is not valid for this job state.",
    action: "Refresh the recorded official job status before taking another action.",
  },
  swissmodel_lifecycle_status_unavailable: {
    message: "The official lifecycle status is unavailable.",
    action: "Retry only after documented official lifecycle support is available.",
  },
};

const SAFE_IDENTIFIER = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/;
const SAFE_FINGERPRINT = /^[0-9a-f]{64}$/;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function safeIdentifier(value: unknown): string | undefined {
  if (typeof value !== "string") return undefined;
  const candidate = value.trim();
  return SAFE_IDENTIFIER.test(candidate) ? candidate : undefined;
}

function safeFingerprint(value: unknown): string | undefined {
  if (typeof value !== "string") return undefined;
  const candidate = value.trim().toLowerCase();
  return SAFE_FINGERPRINT.test(candidate) ? candidate : undefined;
}

function safeTimestamp(value: unknown): string | undefined {
  if (typeof value !== "string") return undefined;
  const timestamp = new Date(value);
  return Number.isNaN(timestamp.getTime()) ? undefined : timestamp.toISOString();
}

function safeModelUrl(value: unknown): string | undefined {
  if (typeof value !== "string") return undefined;
  try {
    const url = new URL(value.trim());
    if (
      url.protocol !== "https:"
      || url.hostname !== "swissmodel.expasy.org"
      || (url.port !== "" && url.port !== "443")
      || url.username
      || url.password
      || url.search
      || url.hash
    ) {
      return undefined;
    }
    return url.toString();
  } catch {
    return undefined;
  }
}

function fallbackLifecycle(): OfficialLifecycleStatus {
  return {
    status: "paused",
    provider: "swissmodel",
    messageCode: "swissmodel_lifecycle_status_unavailable",
    ...MESSAGE_COPY.swissmodel_lifecycle_status_unavailable,
  };
}

/**
 * Reconstruct a display-safe lifecycle record from untrusted API/result data.
 * Incoming prose is never rendered: known message codes and states map to
 * local fixed copy, and only allowlisted provenance fields survive.
 */
export function parseOfficialLifecycle(value: unknown): OfficialLifecycleStatus {
  if (!isRecord(value) || value.provider !== "swissmodel") return fallbackLifecycle();
  const rawState = value.status;
  if (typeof rawState !== "string" || !LIFECYCLE_STATES.has(rawState as OfficialLifecycleState)) {
    return fallbackLifecycle();
  }

  const status = rawState as OfficialLifecycleState;
  const messageCode = typeof value.messageCode === "string" && MESSAGE_COPY[value.messageCode]
    ? value.messageCode
    : undefined;
  const presentation = messageCode ? MESSAGE_COPY[messageCode] : STATE_COPY[status];
  const lifecycle: OfficialLifecycleStatus = {
    status,
    provider: "swissmodel",
    ...presentation,
  };
  if (messageCode) lifecycle.messageCode = messageCode;

  const identifiers: Array<[keyof Pick<OfficialLifecycleStatus, "method" | "selectionId" | "requestId" | "modelId">, unknown]> = [
    ["method", value.method],
    ["selectionId", value.selectionId],
    ["requestId", value.requestId],
    ["modelId", value.modelId],
  ];
  identifiers.forEach(([key, raw]) => {
    const safe = safeIdentifier(raw);
    if (safe) lifecycle[key] = safe;
  });

  const fingerprint = safeFingerprint(value.mevFingerprint);
  if (fingerprint) lifecycle.mevFingerprint = fingerprint;
  const modelUrl = safeModelUrl(value.modelUrl);
  if (modelUrl) lifecycle.modelUrl = modelUrl;
  if (value.pdbQualified === true) lifecycle.pdbQualified = true;

  const timestamps: Array<[keyof Pick<OfficialLifecycleStatus, "submittedAt" | "updatedAt" | "completedAt">, unknown]> = [
    ["submittedAt", value.submittedAt],
    ["updatedAt", value.updatedAt],
    ["completedAt", value.completedAt],
  ];
  timestamps.forEach(([key, raw]) => {
    const safe = safeTimestamp(raw);
    if (safe) lifecycle[key] = safe;
  });
  return lifecycle;
}

/** Return a lifecycle only when a step result actually contains official metadata. */
export function officialLifecycleFromResult(result: unknown): OfficialLifecycleStatus | null {
  if (!isRecord(result) || !Object.prototype.hasOwnProperty.call(result, "officialLifecycle")) {
    return null;
  }
  return parseOfficialLifecycle(result.officialLifecycle);
}

/** Remove every unallowlisted official-provider field before raw UI rendering. */
export function redactOfficialLifecycleResult(result: unknown): unknown {
  const lifecycle = officialLifecycleFromResult(result);
  return lifecycle ? { officialLifecycle: lifecycle } : result;
}

/**
 * Clone a job for reports and exports.  Jobs without official lifecycle data
 * retain their original shape; official lifecycle steps retain only safe status
 * metadata and a fixed message in place of any stored error text.
 */
export function publicJobForPresentation(job: Job): Job {
  let changed = false;
  const phases = job.phases.map((phase) => {
    const steps = phase.steps.map((step) => {
      const lifecycle = officialLifecycleFromResult(step.result);
      if (!lifecycle) return step;
      changed = true;
      return {
        ...step,
        result: { officialLifecycle: lifecycle },
        error: step.error
          ? {
              ...step.error,
              message: lifecycle.message,
              tool: "SWISS-MODEL",
              firstFailedAt: undefined,
              lastFailedAt: undefined,
            }
          : undefined,
      };
    });
    return steps === phase.steps ? phase : { ...phase, steps };
  });
  return changed ? { ...job, phases } : job;
}
