import * as React from "react";
import { parseOfficialLifecycle } from "@/lib/officialLifecycle";
import type { OfficialLifecycleStatus } from "@/types";

const STATE_STYLE: Record<OfficialLifecycleStatus["status"], { label: string; tone: string }> = {
  queued: { label: "Queued", tone: "border-blue-200 bg-blue-50 text-blue-800" },
  running: { label: "Running", tone: "border-sky-200 bg-sky-50 text-sky-800" },
  paused: { label: "Paused", tone: "border-amber-200 bg-amber-50 text-amber-900" },
  failed: { label: "Failed", tone: "border-red-200 bg-red-50 text-red-900" },
  succeeded: { label: "Succeeded", tone: "border-emerald-200 bg-emerald-50 text-emerald-900" },
};

function formatTimestamp(value: string): string {
  const timestamp = new Date(value);
  return Number.isNaN(timestamp.getTime()) ? "" : timestamp.toLocaleString();
}

function compactFingerprint(fingerprint: string): string {
  return `${fingerprint.slice(0, 12)}…${fingerprint.slice(-8)}`;
}

/**
 * Safe user-facing status for an official lifecycle. The component reparses
 * its input so arbitrary message text or unexpected provenance cannot reach
 * the DOM even if it bypasses the normal API client.
 */
export function OfficialLifecycleStatusPanel({ lifecycle }: { lifecycle: OfficialLifecycleStatus }) {
  const safe = parseOfficialLifecycle(lifecycle);
  const style = STATE_STYLE[safe.status];
  const metadata = [
    ["Method", safe.method],
    ["Selection ID", safe.selectionId],
    ["Request ID", safe.requestId],
    ["Model ID", safe.modelId],
    ["MEV fingerprint", safe.mevFingerprint ? compactFingerprint(safe.mevFingerprint) : undefined],
    ["Submitted", safe.submittedAt ? formatTimestamp(safe.submittedAt) : undefined],
    ["Updated", safe.updatedAt ? formatTimestamp(safe.updatedAt) : undefined],
    ["Completed", safe.completedAt ? formatTimestamp(safe.completedAt) : undefined],
  ].filter((entry): entry is [string, string] => Boolean(entry[1]));

  return (
    <section
      aria-label="Official SWISS-MODEL status"
      className={`mt-2 rounded-md border px-3 py-2 text-[11px] ${style.tone}`}
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="font-semibold">Official SWISS-MODEL status</p>
        <span className="rounded-full border border-current/20 bg-white/50 px-2 py-0.5 text-[10px] font-bold uppercase tracking-wide">
          {style.label}
        </span>
      </div>
      <p className="mt-1">{safe.message}</p>
      <p className="mt-1 text-[10px]"><span className="font-semibold">Next action:</span> {safe.action}</p>
      {(metadata.length > 0 || safe.modelUrl || safe.pdbQualified) && (
        <dl className="mt-2 grid gap-x-4 gap-y-1 border-t border-current/15 pt-2 text-[10px] sm:grid-cols-2">
          <div><dt className="inline font-semibold">Provider:</dt> <dd className="inline">SWISS-MODEL</dd></div>
          {metadata.map(([label, value]) => (
            <div key={label} className="min-w-0 truncate"><dt className="inline font-semibold">{label}:</dt> <dd className="inline">{value}</dd></div>
          ))}
          {safe.modelUrl && (
            <div className="min-w-0 truncate">
              <dt className="inline font-semibold">Model URL:</dt>{" "}
              <dd className="inline"><a className="underline" href={safe.modelUrl} rel="noreferrer" target="_blank">Open provider model</a></dd>
            </div>
          )}
          {safe.pdbQualified && <div><dt className="inline font-semibold">PDB qualification:</dt> <dd className="inline">Verified</dd></div>}
        </dl>
      )}
    </section>
  );
}
