/** inference-service JobStatus names → the StatusChip variant vocabulary
 * (SUCCEEDED/RUNNING/QUEUED/FAILED/CANCELLED/PENDING) so the live dot + colors
 * match the rest of the app. Lives in lib/ because page files may only export
 * Next.js page fields (enforced from Next 15.5). */
const STATUS_UI: Record<string, string> = {
  validating: "PENDING",
  queued: "QUEUED",
  submitted: "QUEUED",
  running: "RUNNING",
  finalizing: "RUNNING",
  succeeded: "SUCCEEDED",
  failed: "FAILED",
  rejected: "FAILED",
  cancelling: "RUNNING",
  cancelled: "CANCELLED",
};

export function inferenceStatusUi(status?: string | null): string | null {
  if (!status) return null;
  return STATUS_UI[status] ?? status.toUpperCase();
}
