import { JobForm } from "@/components/forms/JobForm";

export default function NewJobPage() {
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-foreground">New Pipeline Job</h1>
        <p className="text-sm text-muted-foreground">
          Configure a 14-phase multi-epitope vaccine design run
        </p>
      </div>
      <JobForm />
    </div>
  );
}