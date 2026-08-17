"use client";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Separator } from "@/components/ui/separator";

const PHASES = [
  { n: 1, name: "Proteome Retrieval & Redundancy Removal" },
  { n: 2, name: "Subtractive Proteomics Filtering" },
  { n: 3, name: "Antigenicity & Safety Screening" },
  { n: 4, name: "Structural Prediction & Validation" },
  { n: 5, name: "CTL (CD8+ T-Cell) Epitope Prediction & Filtering" },
  { n: 6, name: "HTL (CD4+ T-Cell) Epitope Prediction & Filtering" },
  { n: 7, name: "B-Cell Epitope Prediction & Filtering" },
  { n: 8, name: "Population Coverage & Epitope Overlap" },
  { n: 9, name: "MEV Construct Assembly" },
  { n: 10, name: "MEV Construct Validation" },
  { n: 11, name: "MEV 3D Structure & Validation" },
  { n: 12, name: "Disulfide Bond Engineering" },
  { n: 13, name: "Codon Optimization & In Silico Cloning" },
  { n: 14, name: "Immune Simulation" },
];

export default function DocsPage() {
  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-foreground">Documentation</h1>
        <p className="text-sm text-muted-foreground">
          How the 14-phase multi-epitope vaccine design pipeline works
        </p>
      </div>

      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="flex items-center justify-between text-base">
            Pipeline overview
            <Badge variant="secondary">14 phases · 41 steps</Badge>
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-1 text-sm">
          <p className="text-muted-foreground">
            A job walks a pathogen proteome through redundancy removal, subtractive proteomics,
            safety screening and structural validation before predicting CTL, HTL and B-cell
            epitopes. Strong candidates are assembled into a single poly-epitope construct with
            linkers and an adjuvant, validated <em>in silico</em>, engineered, codon-optimized
            and finally tested in an immune simulation.
          </p>
          <Separator className="my-3" />
          <ol className="space-y-1.5">
            {PHASES.map((p) => (
              <li key={p.n} className="flex items-center gap-3">
                <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-primary/10 font-mono text-xs font-semibold text-primary">
                  {p.n}
                </span>
                <span className="text-sm text-foreground">{p.name}</span>
              </li>
            ))}
          </ol>
        </CardContent>
      </Card>

      <div className="grid gap-4 sm:grid-cols-2">
        <Card>
          <CardHeader className="pb-1">
            <CardTitle className="text-sm">Real-time updates</CardTitle>
          </CardHeader>
          <CardContent className="text-xs text-muted-foreground">
            The frontend connects to <code className="font-mono">/ws/pipeline/&#123;jobId&#125;</code> over
            WebSocket for live step events. While a job is running, the client also polls <code className="font-mono">GET /api/jobs/&#123;jobId&#125;</code> every 5s as a fallback.
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="pb-1">
            <CardTitle className="text-sm">Exports</CardTitle>
          </CardHeader>
          <CardContent className="text-xs text-muted-foreground">
            Completed jobs export epitope tables (CSV), the vaccine construct (FASTA / GenBank), an
            HTML summary report and a full archive manifest via the toolbar menu. The payloads are
            assembled client-side from the real job record returned by <code className="font-mono">GET /api/jobs/&#123;jobId&#125;</code>.
          </CardContent>
        </Card>
      </div>
    </div>
  );
}