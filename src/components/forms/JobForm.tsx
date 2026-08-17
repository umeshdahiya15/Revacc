"use client";

import { useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import {
  ArrowLeft,
  ArrowRight,
  Beaker,
  FileUp,
  FlaskConical,
  Play,
  RotateCcw,
  Settings2,
  UploadCloud,
} from "lucide-react";
import {
  ADJUVANTS,
  DEFAULT_HLA_MHC1,
  DEFAULT_HLA_MHC2,
  HLA_MHC1_COMMON,
  HLA_MHC2_COMMON,
} from "@/lib/constants";
import { cn } from "@/lib/utils";
import { apiRequest } from "@/lib/api";
import { useToast } from "@/hooks/useToast";
import { PathogenSearch, type PathogenSelection } from "@/components/forms/PathogenSearch";
import { AlleleSelector } from "@/components/forms/AlleleSelector";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Slider } from "@/components/ui/slider";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { Checkbox } from "@/components/ui/checkbox";
import { Badge } from "@/components/ui/badge";

interface FormState {
  name: string;
  pathogen: PathogenSelection;
  fastaFile: string | null;
  adjuvant: string;
  host: string;
  vector: string;
  hlaA: string[];
  hlaB: string[];
  hlaDrb: string[];
  blast: number[];
  epitmer: number[];
  vepred: number[];
  bcellWindow: number[];
  useCtb: boolean;
  useFlexLinker: boolean;
  useKKLinker: boolean;
}

const HLA_A = HLA_MHC1_COMMON.filter((a) => a.startsWith("HLA-A"));
const HLA_B = HLA_MHC1_COMMON.filter((a) => a.startsWith("HLA-B"));
const HLA_II = HLA_MHC2_COMMON;
const DEFAULT_A = DEFAULT_HLA_MHC1.filter((a) => a.startsWith("HLA-A"));
const DEFAULT_B = DEFAULT_HLA_MHC1.filter((a) => a.startsWith("HLA-B"));
const DEFAULT_II = DEFAULT_HLA_MHC2;

const DEFAULT_STATE: FormState = {
  name: "",
  pathogen: { name: "Streptococcus agalactiae", strain: "GBS 2603V/R", taxonId: 208435 },
  fastaFile: null,
  adjuvant: "ctxb",
  host: "human",
  vector: "DNA",
  hlaA: DEFAULT_A,
  hlaB: DEFAULT_B,
  hlaDrb: DEFAULT_II,
  blast: [0.5],
  epitmer: [2.0],
  vepred: [0.5],
  bcellWindow: [16],
  useCtb: true,
  useFlexLinker: true,
  useKKLinker: true,
};

const STEPS = ["Pathogen & Input", "Pipeline Settings", "Advanced Parameters"];

export function JobForm() {
  const router = useRouter();
  const { showToast } = useToast();
  const [step, setStep] = useState(0);
  const [form, setForm] = useState<FormState>(DEFAULT_STATE);

  const set = (patch: Partial<FormState>) => setForm((f) => ({ ...f, ...patch }));

  const canNext = useMemo(() => {
    if (step === 0) return form.pathogen.name.trim().length > 0;
    if (step === 1)
      return form.hlaA.length > 0 && form.hlaB.length > 0 && form.hlaDrb.length > 0;
    return true;
  }, [step, form]);

  const submit = async () => {
    const payload = {
      name: form.name || `${form.pathogen.name} ${form.pathogen.strain ?? "MEV"}`.trim(),
      pathogenName: form.pathogen.name,
      strain: form.pathogen.strain ?? null,
      taxonId: form.pathogen.taxonId ?? null,
      fastaFileName: form.fastaFile,
      adjuvant: form.adjuvant,
      expressionHost: form.host,
      expressionVector: form.vector,
      hlaMhc1: [...form.hlaA, ...form.hlaB],
      hlaMhc2: form.hlaDrb,
      bCellWindow: form.bcellWindow[0],
      mhciPercentile: form.blast[0],
      mhciiPercentile: form.epitmer[0],
      cdHitThreshold: 0.8,
      vaxijenThreshold: form.vepred[0],
      reviewedOnly: false,
      runImmuneSim: true,
      runDisulfide: form.useKKLinker,
      enableCoverage: true,
      coverageRegions: [],
    };
    try {
      const created = await apiRequest<{ id: string }>("/api/jobs", {
        method: "POST",
        body: payload,
        timeoutMs: 8000,
      });
      try {
        await apiRequest(`/api/jobs/${created.id}/start`, { method: "POST", timeoutMs: 8000 });
        showToast("Pipeline started", "success", `Job ${created.id} is now running.`);
      } catch {
        showToast("Job created", "info", `Job ${created.id} was created — press Start on the job page.`);
      }
      router.push(`/jobs/${created.id}`);
    } catch (e) {
      showToast("Could not create job", "error", e instanceof Error ? e.message : "Backend unreachable.");
    }
  };

  return (
    <div className="mx-auto max-w-3xl space-y-6">
      {/* Stepper */}
      <div className="flex items-center gap-2">
        {STEPS.map((label, i) => (
          <div key={label} className="flex flex-1 items-center gap-2">
            <button
              type="button"
              onClick={() => i < step && setStep(i)}
              className={cn(
                "flex h-7 w-7 items-center justify-center rounded-full text-xs font-semibold transition-colors",
                i === step && "bg-primary text-primary-foreground",
                i < step && "bg-primary/15 text-primary hover:bg-primary/25",
                i > step && "bg-muted text-muted-foreground",
              )}
            >
              {i < step ? <RotateCcw className="h-3.5 w-3.5" /> : i + 1}
            </button>
            <span
              className={cn(
                "text-xs font-medium",
                i === step ? "text-foreground" : "text-muted-foreground",
              )}
            >
              {label}
            </span>
          </div>
        ))}
      </div>

      <div className="rounded-xl border border-border bg-card p-6">
        {step === 0 && <StepPathogen form={form} set={set} />}
        {step === 1 && <StepPipeline form={form} set={set} />}
        {step === 2 && <StepAdvanced form={form} set={set} />}
      </div>

      {/* Nav */}
      <div className="flex items-center justify-between">
        <Button
          variant="ghost"
          onClick={() => (step === 0 ? router.push("/jobs") : setStep(step - 1))}
        >
          <ArrowLeft className="h-4 w-4" />
          {step === 0 ? "Jobs" : "Back"}
        </Button>
        {step < STEPS.length - 1 ? (
          <Button disabled={!canNext} onClick={() => setStep(step + 1)}>
            Next
            <ArrowRight className="h-4 w-4" />
          </Button>
        ) : (
          <Button disabled={!canNext} onClick={submit}>
            <Play className="h-4 w-4" />
            Start Pipeline
          </Button>
        )}
      </div>
    </div>
  );
}

function StepPathogen({
  form,
  set,
}: {
  form: FormState;
  set: (patch: Partial<FormState>) => void;
}) {
  return (
    <div className="space-y-5">
      <div className="flex items-center gap-2">
        <Beaker className="h-4 w-4 text-primary" />
        <h2 className="text-sm font-semibold text-foreground">Pathogen & Input</h2>
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        <div className="sm:col-span-2">
          <Label className="mb-1.5 block text-xs font-medium text-foreground">
            Pathogen (autocomplete from NCBI taxonomy)
          </Label>
          <PathogenSearch
            value={form.pathogen}
            onChange={(pathogen) => set({ pathogen })}
          />
{form.pathogen.strain && (
                <p className="mt-1.5 text-xs text-muted-foreground">
                  {form.pathogen.name} <Badge variant="secondary">{form.pathogen.strain}</Badge> · NCBI
                  txid {form.pathogen.taxonId}
                </p>
              )}
        </div>

        <div className="sm:col-span-2">
          <Label className="mb-1.5 block text-xs font-medium text-foreground">Job name</Label>
          <Input
            value={form.name}
            onChange={(e) => set({ name: e.target.value })}
            placeholder={form.pathogen.strain ? `${form.pathogen.name} ${form.pathogen.strain} MEV` : "Unnamed MEV job"}
          />
        </div>

        <div className="sm:col-span-2">
          <Label className="mb-1.5 block text-xs font-medium text-foreground">
            Reference proteome (FASTA, optional)
          </Label>
          <input
            id="fasta-input"
            type="file"
            accept=".fasta,.faa,.fa,.txt"
            className="hidden"
            onChange={(e) => set({ fastaFile: e.target.files?.[0]?.name ?? null })}
          />
          <label
            htmlFor="fasta-input"
            className={cn(
              "flex cursor-pointer flex-col items-center justify-center gap-2 rounded-lg border-2 border-dashed px-4 py-8 text-center transition-colors",
              form.fastaFile
                ? "border-primary/40 bg-primary/5"
                : "border-border hover:border-primary/40 hover:bg-accent/40",
            )}
          >
            {form.fastaFile ? (
              <>
                <FileUp className="h-5 w-5 text-primary" />
                <p className="text-xs font-medium text-foreground">{form.fastaFile}</p>
                <p className="text-[11px] text-muted-foreground">
                  Recorded as the reference proteome for this job
                </p>
              </>
            ) : (
              <>
                <UploadCloud className="h-6 w-6 text-muted-foreground" />
                <p className="text-sm font-medium text-foreground">
                  Drop FASTA file or click to browse
                </p>
                <p className="text-[11px] text-muted-foreground">
                  Proteome FASTA will be fetched from NCBI for {form.pathogen.taxonId} if omitted
                </p>
              </>
            )}
          </label>
        </div>
      </div>
    </div>
  );
}

function StepPipeline({
  form,
  set,
}: {
  form: FormState;
  set: (patch: Partial<FormState>) => void;
}) {
  return (
    <div className="space-y-6">
      <div className="flex items-center gap-2">
        <FlaskConical className="h-4 w-4 text-primary" />
        <h2 className="text-sm font-semibold text-foreground">Pipeline Settings</h2>
      </div>

      {/* Adjuvant */}
      <div>
        <Label className="mb-2 block text-xs font-medium text-foreground">Adjuvant</Label>
        <RadioGroup
          value={form.adjuvant}
          onValueChange={(v) => set({ adjuvant: v })}
          className="grid grid-cols-2 gap-2 sm:grid-cols-4"
        >
          {ADJUVANTS.map((a) => (
            <label
              key={a.id}
              className={cn(
                "flex cursor-pointer items-center gap-2 rounded-lg border border-border p-3 transition-colors",
                form.adjuvant === a.id ? "border-primary/60 bg-primary/5" : "hover:bg-accent",
              )}
            >
              <RadioGroupItem value={a.id} className="sr-only" />
              <div className="min-w-0">
                <p className="truncate text-xs font-semibold text-foreground">{a.label}</p>
                <p className="text-[10px] text-muted-foreground">
                  {a.uniprot ? `UniProt ${a.uniprot}` : a.default ? "Recommended" : "Optional"}
                </p>
              </div>
            </label>
          ))}
        </RadioGroup>
      </div>

      {/* Host + vector */}
      <div className="grid gap-4 sm:grid-cols-2">
        <div>
          <Label className="mb-2 block text-xs font-medium text-foreground">Target host</Label>
          <RadioGroup
            value={form.host}
            onValueChange={(v) => set({ host: v })}
            className="flex gap-2"
          >
            {["human", "bovine", "mouse", "other"].map((h) => (
              <label
                key={h}
                className={cn(
                  "flex cursor-pointer items-center gap-1.5 rounded-lg border border-border px-3 py-2 text-xs capitalize",
                  form.host === h ? "border-primary/60 bg-primary/5 font-medium" : "hover:bg-accent",
                )}
              >
                <RadioGroupItem value={h} className="sr-only" />
                {h}
              </label>
            ))}
          </RadioGroup>
        </div>

        <div>
          <Label className="mb-2 block text-xs font-medium text-foreground">Delivery vector</Label>
          <RadioGroup
            value={form.vector}
            onValueChange={(v) => set({ vector: v })}
            className="flex gap-2"
          >
            {["DNA", "mRNA", "Viral"].map((v) => (
              <label
                key={v}
                className={cn(
                  "flex cursor-pointer items-center gap-1.5 rounded-lg border border-border px-3 py-2 text-xs",
                  form.vector === v
                    ? "border-primary/60 bg-primary/5 font-medium"
                    : "hover:bg-accent",
                )}
              >
                <RadioGroupItem value={v} className="sr-only" />
                {v}
              </label>
            ))}
          </RadioGroup>
        </div>
      </div>

      {/* Thresholds */}
      <div className="space-y-4">
        <Label className="flex items-center gap-2 text-xs font-medium text-foreground">
          <Settings2 className="h-3.5 w-3.5" />
          Binding & antigenicity thresholds
        </Label>

        <ThresholdSlider
          label="NetMHCpan rank cutoff (class I)"
          hint="Default 0.5% · stronger cutoffs filter more epitopes"
          value={form.blast}
          min={0.05}
          max={2}
          step={0.05}
          unit="%"
          onValueChange={(v) => set({ blast: v })}
        />
        <ThresholdSlider
          label="NetMHCIIpan rank cutoff (class II)"
          hint="Default 2.0% · stricter for DRB coverage"
          value={form.epitmer}
          min={0.5}
          max={10}
          step={0.5}
          unit="%"
          onValueChange={(v) => set({ epitmer: v })}
        />
        <ThresholdSlider
          label="VaxiJen 2.0 protective-antigen threshold"
          hint="Proteins scoring below this are dropped"
          value={form.vepred}
          min={0.2}
          max={1}
          step={0.01}
          unit=""
          onValueChange={(v) => set({ vepred: v })}
        />
      </div>
    </div>
  );
}

function StepAdvanced({
  form,
  set,
}: {
  form: FormState;
  set: (patch: Partial<FormState>) => void;
}) {
  return (
    <div className="space-y-6">
      <div className="flex items-center gap-2">
        <Settings2 className="h-4 w-4 text-primary" />
        <h2 className="text-sm font-semibold text-foreground">Advanced Parameters</h2>
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        <AlleleSelector
          label="MHC class I · HLA-A alleles"
          popoverTitle="HLA-A alleles"
          alleles={HLA_A}
          selected={form.hlaA}
          onChange={(hlaA) => set({ hlaA })}
        />
        <AlleleSelector
          label="MHC class I · HLA-B alleles"
          popoverTitle="HLA-B alleles"
          alleles={HLA_B}
          selected={form.hlaB}
          onChange={(hlaB) => set({ hlaB })}
        />
        <AlleleSelector
          label="MHC class II · DRB alleles"
          popoverTitle="HLA class II alleles"
          alleles={HLA_II}
          selected={form.hlaDrb}
          onChange={(hlaDrb) => set({ hlaDrb })}
        />
      </div>

      <ThresholdSlider
        label="B-cell epitope window"
        hint="ElliPro sliding window (residues)"
        value={form.bcellWindow}
        min={8}
        max={24}
        step={1}
        unit=" aa"
        onValueChange={(v) => set({ bcellWindow: v })}
      />

      <div className="space-y-2">
        <Label className="block text-xs font-medium text-foreground">Construct linker options</Label>
        <div className="grid gap-2 sm:grid-cols-3">
          <LinkerCheck label="CTxB signal (21 aa)" checked={form.useCtb} onChange={(v) => set({ useCtb: v })} />
          <LinkerCheck label="Flexible linker (GGGGS)" checked={form.useFlexLinker} onChange={(v) => set({ useFlexLinker: v })} />
          <LinkerCheck label="KK B-cell spacer" checked={form.useKKLinker} onChange={(v) => set({ useKKLinker: v })} />
        </div>
      </div>
    </div>
  );
}

function ThresholdSlider({
  label,
  hint,
  value,
  min,
  max,
  step,
  unit,
  onValueChange,
}: {
  label: string;
  hint: string;
  value: number[];
  min: number;
  max: number;
  step: number;
  unit: string;
  onValueChange: (value: number[]) => void;
}) {
  return (
    <div>
      <div className="flex items-baseline justify-between">
        <Label className="text-xs font-medium text-foreground">{label}</Label>
        <div className="flex items-center gap-1">
          <Input
            type="number"
            value={value[0]}
            min={min}
            max={max}
            step={step}
            aria-label={`${label} value`}
            className="h-7 w-20 px-2 text-right font-mono text-[11px]"
            onChange={(event) => {
              const next = Number(event.target.value);
              if (!Number.isFinite(next)) return;
              onValueChange([Math.min(max, Math.max(min, next))]);
            }}
          />
          <Badge variant="outline" className="font-mono text-[11px]">
            {unit}
          </Badge>
        </div>
      </div>
      <Slider
        min={min}
        max={max}
        step={step}
        value={value}
        onValueChange={onValueChange}
        className="mt-1.5"
      />
      <p className="mt-1 text-[11px] text-muted-foreground">{hint}</p>
    </div>
  );
}

function LinkerCheck({ label, checked, onChange }: { label: string; checked: boolean; onChange: (v: boolean) => void }) {
  return (
    <label
      className={cn(
        "flex cursor-pointer items-center gap-2 rounded-lg border border-border px-3 py-2 text-xs",
        checked ? "border-primary/60 bg-primary/5 text-foreground" : "text-muted-foreground hover:bg-accent",
      )}
    >
      <Checkbox checked={checked} onCheckedChange={(v) => onChange(v === true)} className="h-4 w-4" />
      <span className="font-medium">{label}</span>
    </label>
  );
}
