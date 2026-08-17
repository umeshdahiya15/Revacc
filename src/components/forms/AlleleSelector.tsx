"use client";

import { useState } from "react";
import { Check, ChevronsUpDown } from "lucide-react";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Label } from "@/components/ui/label";
import { Checkbox } from "@/components/ui/checkbox";

export function AlleleSelector({
  label,
  alleles,
  selected,
  onChange,
  popoverTitle,
}: {
  label: string;
  alleles: string[];
  selected: string[];
  onChange: (value: string[]) => void;
  popoverTitle?: string;
}) {
  const [open, setOpen] = useState(false);

  const toggle = (allele: string) => {
    onChange(
      selected.includes(allele)
        ? selected.filter((a) => a !== allele)
        : [...selected, allele],
    );
  };

  const selectAll = () => {
    onChange(alleles.length === selected.length ? [] : [...alleles]);
  };

  return (
    <div>
      <Label className="mb-1.5 block text-xs font-medium text-foreground">{label}</Label>
      <Popover open={open} onOpenChange={setOpen}>
        <PopoverTrigger asChild>
          <Button
            variant="outline"
            role="combobox"
            aria-expanded={open}
            className="w-full justify-between font-normal"
          >
            <span className="truncate">
              {selected.length > 0
                ? `${selected.length} allele${selected.length > 1 ? "s" : ""} selected · ${selected.slice(0, 3).join(", ")}${selected.length > 3 ? "…" : ""}`
                : "No alleles selected"}
            </span>
            <ChevronsUpDown className="ml-2 h-4 w-4 shrink-0 opacity-50" />
          </Button>
        </PopoverTrigger>
        <PopoverContent align="start" className="w-80 p-0">
          <div className="flex items-center justify-between border-b border-border px-3 py-2">
            <p className="text-xs font-medium text-foreground">
              {popoverTitle ?? label}
            </p>
            <button
              type="button"
              onClick={selectAll}
              className="text-[11px] font-medium text-primary hover:underline"
            >
              {alleles.length === selected.length ? "Clear all" : "Select all"}
            </button>
          </div>
          <div className="max-h-56 overflow-y-auto p-1.5">
            {alleles.map((allele) => {
              const checked = selected.includes(allele);
              return (
                <label
                  key={allele}
                  className={cn(
                    "flex cursor-pointer items-center gap-2.5 rounded-md px-2 py-1.5 text-sm transition-colors hover:bg-accent",
                    checked && "bg-accent/60",
                  )}
                >
                  <Checkbox
                    checked={checked}
                    onCheckedChange={() => toggle(allele)}
                    className="h-4 w-4"
                  />
                  <span className="font-mono text-xs text-foreground">{allele}</span>
                </label>
              );
            })}
          </div>
          <div className="border-t border-border px-3 py-2">
            <Button size="sm" className="w-full" onClick={() => setOpen(false)}>
              <Check className="mr-1.5 h-3.5 w-3.5" />
              Done ({selected.length})
            </Button>
          </div>
        </PopoverContent>
      </Popover>
    </div>
  );
}