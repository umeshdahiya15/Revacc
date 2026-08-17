import { cn } from "@/lib/utils";
import { CheckCircle2, CircleDashed, Loader2, XCircle } from "lucide-react";
import { motion } from "framer-motion";
import type { StepStatus } from "@/types";

export function StepProgressIndicator({
  status,
  percent,
  size = "md",
}: {
  status: StepStatus;
  percent?: number;
  size?: "sm" | "md";
}) {
  const dims = size === "sm" ? "h-5 w-5" : "h-6 w-6";

  if (status === "running") {
    return (
      <span className={cn("relative inline-flex items-center justify-center", dims)}>
        <Loader2 className="h-full w-full animate-spin text-blue-500" />
        {typeof percent === "number" && percent > 0 && (
          <span className="absolute inset-0 flex items-center justify-center text-[8px] font-semibold text-blue-700">
            {Math.round(percent)}
          </span>
        )}
      </span>
    );
  }

  if (status === "success") {
    return (
      <motion.span
        initial={{ scale: 0.4, opacity: 0 }}
        animate={{ scale: 1, opacity: 1 }}
        transition={{ type: "spring", stiffness: 400, damping: 20 }}
        className={cn("inline-flex items-center justify-center", dims)}
      >
        <CheckCircle2 className="h-full w-full text-emerald-500" />
      </motion.span>
    );
  }

  if (status === "failed") {
    return (
      <motion.span
        initial={{ x: 0 }}
        animate={{ x: [0, -3, 3, -3, 3, 0] }}
        transition={{ duration: 0.4 }}
        className={cn("inline-flex items-center justify-center", dims)}
      >
        <XCircle className="h-full w-full text-red-500" />
      </motion.span>
    );
  }

  if (status === "paused") {
    return (
      <span className={cn("inline-flex items-center justify-center", dims)}>
        <span className="h-3 w-3 rounded-full border-2 border-amber-500" />
      </span>
    );
  }

  return (
    <span className={cn("inline-flex items-center justify-center", dims)}>
      <CircleDashed className="h-full w-full text-slate-300" />
    </span>
  );
}