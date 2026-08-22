"use client";

import { useState } from "react";
import { Check, Info, Trash2 } from "lucide-react";
import { cn } from "@/lib/utils";
import { useToast } from "@/hooks/useToast";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Separator } from "@/components/ui/separator";
import { Badge } from "@/components/ui/badge";
import { DEFAULT_API_URL } from "@/lib/api";

const STORAGE_KEY = "revacc:settings";

interface Settings {
  apiUrl: string;
  polling: boolean;
  autoplay: boolean;
  compactSteps: boolean;
}

const DEFAULTS: Settings = {
  apiUrl: process.env.NEXT_PUBLIC_API_URL ?? DEFAULT_API_URL,
  polling: true,
  autoplay: true,
  compactSteps: false,
};

function loadSettings(): Settings {
  if (typeof window === "undefined") return DEFAULTS;
  try {
    return { ...DEFAULTS, ...JSON.parse(localStorage.getItem(STORAGE_KEY) ?? "{}") };
  } catch {
    return DEFAULTS;
  }
}

function saveSettings(settings: Settings) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(settings));
}

function Row({
  title,
  description,
  children,
}: {
  title: string;
  description: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-4 py-3">
      <div className="min-w-0">
        <p className="text-sm font-medium text-foreground">{title}</p>
        <p className="text-xs text-muted-foreground">{description}</p>
      </div>
      <div className="shrink-0">{children}</div>
    </div>
  );
}

export default function SettingsPage() {
  const { showToast } = useToast();
  const [settings, setSettings] = useState<Settings>(loadSettings);

  const update = (next: Partial<Settings>) => setSettings((prev) => ({ ...prev, ...next }));
  const setToggle = (key: keyof Settings) => (v: boolean) => update({ [key]: v });

  const save = () => {
    saveSettings(settings);
    showToast("Settings saved", "success", "Preferences stored locally.");
  };

  const reset = () => {
    localStorage.removeItem(STORAGE_KEY);
    setSettings(DEFAULTS);
    showToast("Settings reset", "info", "Preferences reset to defaults.");
  };

  return (
    <div className="mx-auto max-w-2xl space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-foreground">Settings</h1>
        <p className="text-sm text-muted-foreground">Pipeline API connection and UI preferences</p>
      </div>

      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-base">API Connection</CardTitle>
          <CardDescription className="text-xs">
            The React app talks to the FastAPI backend through this base URL.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div>
            <Label htmlFor="api-url" className="mb-1.5 block text-xs font-medium">Base URL</Label>
            <Input
              id="api-url"
              value={settings.apiUrl}
              onChange={(e) => update({ apiUrl: e.target.value })}
              className="font-mono text-sm"
            />
            <p className="mt-1 flex items-center gap-1.5 text-[11px] text-muted-foreground">
              <Info className="h-3 w-3" />
              Also set via NEXT_PUBLIC_API_URL in .env.local
            </p>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-base">Real-time & UI</CardTitle>
          <CardDescription className="text-xs">
            WebSocket live updates and UI behaviour for the pipeline viewer.
          </CardDescription>
        </CardHeader>
        <CardContent className="divide-y divide-border">
          <Row title="Polling fallback" description="Poll GET /api/jobs/{id} every 5s when WebSocket disconnects">
            <Switch checked={settings.polling} onCheckedChange={setToggle("polling")} />
          </Row>
          <Row title="Auto-open phases" description="Expand running/failed phases automatically">
            <Switch checked={settings.autoplay} onCheckedChange={setToggle("autoplay")} />
          </Row>
          <Row title="Compact step rows" description="Show only failed and running steps when collapsed">
            <Switch checked={settings.compactSteps} onCheckedChange={setToggle("compactSteps")} />
          </Row>
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-base">API Keys & Credentials</CardTitle>
          <CardDescription className="text-xs">
            Stored in the backend (.env) — never shipped to the browser.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          {["NCBI E-utilities", "IEDB API", "UniProt", "Benchling"].map((k) => (
            <div key={k} className="flex items-center justify-between rounded-lg border border-border px-3 py-2">
              <span className="text-sm text-foreground">{k}</span>
              <Badge variant="outline" className="gap-1 text-emerald-700">
                <Check className="h-3 w-3" /> Configured
              </Badge>
            </div>
          ))}
        </CardContent>
      </Card>

      <Separator />
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <Button onClick={save}>Save changes</Button>
          <Button variant="ghost" size="sm" onClick={reset}>
            <Trash2 className="h-4 w-4" /> Reset
          </Button>
        </div>
        <p className="text-xs text-muted-foreground">
          {cn("Revacc preferences are stored in your browser's local storage.")}
        </p>
      </div>
    </div>
  );
}
