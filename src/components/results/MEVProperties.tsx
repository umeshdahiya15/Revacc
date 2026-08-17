"use client";

import { BadgeCheck, Download, FileCode2, TriangleAlert, XCircle } from "lucide-react";
import { downloadFile } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { CopyButton } from "@/components/common/CopyButton";
import { Badge } from "@/components/ui/badge";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import type { MEVConstruct, MEVProperty } from "@/types";

const STATUS_BADGE: Record<MEVProperty["status"], { label: string; className: string; icon: typeof BadgeCheck }> = {
  pass: { label: "Pass", className: "bg-emerald-100 text-emerald-700", icon: BadgeCheck },
  warn: { label: "Warn", className: "bg-amber-100 text-amber-700", icon: TriangleAlert },
  fail: { label: "Fail", className: "bg-red-100 text-red-700", icon: XCircle },
  info: { label: "Info", className: "bg-slate-100 text-slate-600", icon: BadgeCheck },
};

export function MEVProperties({
  construct,
}: {
  construct: MEVConstruct;
}) {
  const downloadFasta = () => {
    const fasta = `>MEV_Streptococcus_agalactiae_CTxB_565aa\n${construct.sequence
      .match(/.{1,60}/g)
      ?.join("\n")}\n`;
    downloadFile(fasta, "mev_construct.fasta", "text/plain");
  };

  return (
    <div className="rounded-xl border border-border bg-card p-4">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-sm font-semibold text-foreground">MEV Properties & Validation</h3>
        <div className="flex items-center gap-1.5">
          <CopyButton value={construct.sequence} label="Copy sequence" variant="outline" />
          <Button size="sm" variant="outline" onClick={downloadFasta} className="gap-1.5">
            <FileCode2 className="h-3.5 w-3.5" />
            FASTA
          </Button>
          <Button size="sm" variant="outline" className="gap-1.5">
            <Download className="h-3.5 w-3.5" />
            PDB
          </Button>
        </div>
      </div>

      <div className="overflow-x-auto">
        <Table>
          <TableHeader>
            <TableRow className="hover:bg-transparent">
              <TableHead className="w-1/3">Property</TableHead>
              <TableHead>Value</TableHead>
              <TableHead className="text-right">Status</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {construct.properties.map((prop) => {
              const meta = STATUS_BADGE[prop.status];
              const Icon = meta.icon;
              return (
                <TableRow key={prop.property}>
                  <TableCell className="font-medium text-foreground">{prop.property}</TableCell>
                  <TableCell>
                    <span className="font-mono text-xs">{prop.value}</span>
                    {prop.note && (
                      <span className="ml-2 text-[11px] text-muted-foreground">
                        {prop.note}
                      </span>
                    )}
                  </TableCell>
                  <TableCell className="text-right">
                    <Badge variant="outline" className={meta.className}>
                      <Icon className="h-3 w-3" />
                      {meta.label}
                    </Badge>
                  </TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      </div>
    </div>
  );
}