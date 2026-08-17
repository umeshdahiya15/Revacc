"use client";

import { useMemo, useState } from "react";
import { Search } from "lucide-react";
import { PATHOGEN_SUGGESTIONS } from "@/lib/constants";
import { cn } from "@/lib/utils";
import { Input } from "@/components/ui/input";

export interface PathogenSelection {
  name: string;
  strain?: string;
  taxonId?: number | string;
}

export function PathogenSearch({
  value,
  onChange,
}: {
  value: PathogenSelection;
  onChange: (value: PathogenSelection) => void;
}) {
  const [open, setOpen] = useState(false);

  const matches = useMemo(() => {
    const q = value.name.trim().toLowerCase();
    if (!q) return PATHOGEN_SUGGESTIONS.slice(0, 6);
    return PATHOGEN_SUGGESTIONS.filter(
      (p) => p.name.toLowerCase().includes(q) || p.strain.toLowerCase().includes(q),
    ).slice(0, 6);
  }, [value.name]);

  const select = (name: string, strain: string, taxonId: number) => {
    onChange({ name, strain, taxonId });
    setOpen(false);
  };

  return (
    <div className="relative">
      <div className="relative">
        <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
        <Input
          value={value.name}
          onChange={(e) => {
            onChange({ ...value, name: e.target.value, strain: undefined, taxonId: undefined });
            setOpen(true);
          }}
          onFocus={() => setOpen(true)}
          onBlur={() => setTimeout(() => setOpen(false), 150)}
          placeholder="Type a pathogen name, e.g. Streptococcus agalactiae"
          className="pl-9"
        />
      </div>

      {open && matches.length > 0 && (
        <ul className="absolute z-20 mt-1.5 w-full overflow-hidden rounded-lg border border-border bg-popover shadow-lg">
          {matches.map((p, i) => (
            <li key={`${p.name}-${p.strain}`}>
              <button
                type="button"
                onMouseDown={() => select(p.name, p.strain, p.taxonId)}
                className={cn(
                  "flex w-full items-center justify-between px-3 py-2 text-left text-sm transition-colors hover:bg-accent",
                  i > 0 && "border-t border-border/60",
                )}
              >
                <span className="truncate">
                  <span className="font-medium text-foreground">{p.name}</span>
                  <span className="ml-1 text-xs text-muted-foreground">{p.strain}</span>
                </span>
                <span className="shrink-0 text-[11px] text-muted-foreground">
                  txid {p.taxonId}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}