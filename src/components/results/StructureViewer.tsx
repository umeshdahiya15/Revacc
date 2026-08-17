"use client";

import { useEffect, useRef, useState } from "react";
import { Box, RotateCcw, ZoomIn } from "lucide-react";
import { cn } from "@/lib/utils";
import { LoadingSpinner } from "@/components/common/LoadingSpinner";
import { Button } from "@/components/ui/button";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";

export function StructureViewer({
  pdbData,
  title,
  subtitle,
}: {
  pdbData: string;
  title?: string;
  subtitle?: string;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const stageRef = useRef<{ dispose: () => void; handleResize: () => void } | null>(null);
  const [state, setState] = useState<"loading" | "ready" | "failed" | "unavailable">("loading");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let disposed = false;

    const boot = async () => {
      if (!containerRef.current) return;
      if (typeof window === "undefined") return;

      try {
        const nglModule = await import("ngl");
        const NGL = nglModule.default ?? nglModule;

        const container = containerRef.current;
        container.innerHTML = "";
        const stage = new NGL.Stage(container, {
          backgroundColor: "#f8fafc",
          quality: "medium",
          sampleLevel: 2,
        });
        stageRef.current = stage;

        const comp = await stage.loadFile(pdbData, { ext: "pdb" });
        comp.addRepresentation("cartoon", { color: "spectrum" });
        comp.addRepresentation("surface", { opacity: 0.35, colorScheme: "chainindex" });
        comp.addRepresentation("ball+stick", {
          sele: "not polymer",
          colorValue: "#2563eb",
        });
        stage.autoView();

        if (disposed) {
          stage.dispose();
          return;
        }
        setState("ready");
      } catch (err) {
        console.error("Structure viewer failed:", err);
        if (disposed) return;
        setState("failed");
        setError(err instanceof Error ? err.message : "Unable to initialise 3D viewer");
      }
    };

    const resize = () => {
      stageRef.current?.handleResize();
    };
    const ro = typeof ResizeObserver !== "undefined" ? new ResizeObserver(resize) : null;
    if (ro && containerRef.current) ro.observe(containerRef.current);

    boot();
    return () => {
      disposed = true;
      ro?.disconnect();
      stageRef.current?.dispose();
      stageRef.current = null;
    };
  }, [pdbData]);

  const resetView = () => {
    stageRef.current?.handleResize();
  };

  const residueCount = pdbData.split("\n").filter((l) => l.startsWith("ATOM") && l.slice(12, 16).trim() === "CA").length;

  return (
    <div className="overflow-hidden rounded-xl border border-border bg-card">
      <div className="flex items-center justify-between border-b border-border px-4 py-2.5">
        <div className="flex items-center gap-2">
          <Box className="h-4 w-4 text-primary" />
          <div>
            <h3 className="text-sm font-semibold text-foreground">{title ?? "3D Structure"}</h3>
            {subtitle && <p className="text-[11px] text-muted-foreground">{subtitle}</p>}
          </div>
        </div>
        {state === "ready" && (
          <Button size="sm" variant="outline" onClick={resetView} className="gap-1.5">
            <RotateCcw className="h-3.5 w-3.5" />
            Reset view
          </Button>
        )}
      </div>

      <div className="relative aspect-[16/9] w-full bg-slate-50 md:aspect-[21/9]">
        <div ref={containerRef} className="absolute inset-0" />

        {state !== "ready" && (
          <div className="absolute inset-0 flex flex-col items-center justify-center gap-3 bg-slate-50">
            {state === "loading" && (
              <>
                <LoadingSpinner size="lg" />
                <p className="text-xs text-muted-foreground">Loading structure…</p>
              </>
            )}
            {state === "failed" && (
              <div className="max-w-md px-4">
                <Alert variant="destructive">
                  <AlertTitle>3D viewer unavailable</AlertTitle>
                  <AlertDescription>
                    {error ?? "WebGL may be disabled in this browser."} Showing structure
                    summary instead.
                  </AlertDescription>
                </Alert>
              </div>
            )}
            {state === "unavailable" && (
              <p className="text-xs text-muted-foreground">3D rendering not supported here.</p>
            )}
          </div>
        )}

        {state === "ready" && (
          <div className="pointer-events-none absolute bottom-2 left-2 flex items-center gap-1.5 rounded-md bg-white/90 px-2 py-1 text-[10px] text-muted-foreground shadow-sm">
            <ZoomIn className="h-3 w-3" />
            Drag to rotate · scroll to zoom · right-drag to pan
          </div>
        )}

        {state === "ready" && (
          <div className="pointer-events-none absolute bottom-2 right-2 rounded-md bg-white/90 px-2 py-1 text-[10px] font-medium text-muted-foreground shadow-sm">
            {residueCount} residues · cartoon + surface
          </div>
        )}
      </div>
    </div>
  );
}

export function StructureViewerFallback({ className }: { className?: string }) {
  return (
    <div className={cn("rounded-xl border border-dashed border-border bg-slate-50 p-6 text-center text-sm text-muted-foreground", className)}>
      <Box className="mx-auto mb-2 h-6 w-6" />
      PDB file available — enable WebGL to view the 3D model in-browser.
    </div>
  );
}