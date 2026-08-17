import type { Metadata } from "next";
import "./globals.css";
import { QueryProvider } from "@/components/providers/QueryProvider";
import { AppShell } from "@/components/layout/AppShell";
import { Toaster } from "@/components/ui/sonner";

export const metadata: Metadata = {
  title: {
    default: "Revacc",
    template: "%s · Revacc",
  },
  description:
    "Multi-epitope vaccine design pipeline — automated reverse vaccinology for epitope prediction and vaccine construct assembly.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="en"
      className="antialiased"
      suppressHydrationWarning
    >
      <body className="bg-background text-foreground font-sans">
        <QueryProvider>
          <AppShell>{children}</AppShell>
        </QueryProvider>
        <Toaster position="top-right" richColors />
      </body>
    </html>
  );
}