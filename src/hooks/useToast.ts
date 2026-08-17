"use client";

import { toast } from "sonner";

export type ToastTone = "default" | "success" | "error" | "warning" | "info";

export function useToast() {
  const showToast = (message: string, tone: ToastTone = "default", description?: string) => {
    const options = { description };
    switch (tone) {
      case "success":
        toast.success(message, options);
        break;
      case "error":
        toast.error(message, options);
        break;
      case "warning":
        toast.warning(message, options);
        break;
      case "info":
        toast.info(message, options);
        break;
      default:
        toast(message, options);
    }
  };

  return { showToast };
}