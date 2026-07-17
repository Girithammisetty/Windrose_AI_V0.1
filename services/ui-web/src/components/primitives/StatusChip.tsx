"use client";
import { Radio, PauseCircle } from "lucide-react";
import { Badge } from "@/components/ui/primitives";
import { cn } from "@/lib/utils";
import { t } from "@/lib/i18n/messages";
import { useRealtimeHealth } from "@/stores/ui";

type Variant = "default" | "secondary" | "success" | "warning" | "destructive";

const MAP: Record<string, Variant> = {
  SUCCEEDED: "success",
  RESOLVED: "success",
  CLOSED: "secondary",
  RUNNING: "default",
  IN_PROGRESS: "default",
  QUEUED: "secondary",
  PENDING: "warning",
  DRAFT: "secondary",
  UNASSIGNED: "warning",
  FAILED: "destructive",
  CANCELLED: "secondary",
  LOW: "secondary",
  MEDIUM: "warning",
  HIGH: "warning",
  CRITICAL: "destructive",
};

/**
 * Lifecycle state chip with an SSE-live indicator (UI-FR-019). When the hub
 * connection is degraded it shows a "live updates paused" affordance (BR-5)
 * instead of the pulsing live dot, so a "running" state can never silently rot.
 */
export function StatusChip({
  status,
  live = false,
  className,
}: {
  status?: string | null;
  live?: boolean;
  className?: string;
}) {
  const degraded = useRealtimeHealth((s) => s.degraded);
  if (!status) return <Badge variant="secondary" className={className}>—</Badge>;
  const variant = MAP[status] ?? "default";
  const isActive = ["RUNNING", "IN_PROGRESS", "QUEUED", "PENDING"].includes(status);

  return (
    <Badge variant={variant} className={cn("gap-1", className)}>
      {live && isActive && !degraded && (
        <Radio className="size-3 animate-pulse" aria-label="live" />
      )}
      {live && isActive && degraded && (
        <span className="inline-flex items-center gap-1" title={t("state.livePaused")}>
          <PauseCircle className="size-3" aria-label={t("state.livePaused")} />
        </span>
      )}
      {status.replaceAll("_", " ").toLowerCase()}
    </Badge>
  );
}
