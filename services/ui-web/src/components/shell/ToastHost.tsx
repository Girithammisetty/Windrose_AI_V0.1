"use client";
import { useEffect } from "react";
import { X } from "lucide-react";
import { cn } from "@/lib/utils";
import { useToasts } from "@/stores/ui";

/** Toast host for optimistic-rollback notices (BR-6) and general feedback. */
export function ToastHost() {
  const { toasts, dismiss } = useToasts();
  return (
    <div className="pointer-events-none fixed bottom-4 right-4 z-[60] flex w-80 flex-col gap-2" aria-live="polite">
      {toasts.map((toast) => (
        <ToastItem key={toast.id} id={toast.id} onDismiss={() => dismiss(toast.id)}>
          <div
            className={cn(
              "pointer-events-auto rounded-md border bg-card p-3 shadow-lg",
              toast.variant === "error" && "border-destructive/50",
              toast.variant === "success" && "border-[hsl(var(--success))]/50",
            )}
          >
            <div className="flex items-start justify-between gap-2">
              <div>
                <p className="text-sm font-medium">{toast.title}</p>
                {toast.description && <p className="mt-0.5 text-xs text-muted-foreground">{toast.description}</p>}
                {toast.traceId && (
                  <p className="mt-1 font-mono text-[10px] text-muted-foreground">trace: {toast.traceId}</p>
                )}
              </div>
              <button aria-label="Dismiss" onClick={() => dismiss(toast.id)}>
                <X className="size-4 text-muted-foreground" />
              </button>
            </div>
          </div>
        </ToastItem>
      ))}
    </div>
  );
}

function ToastItem({ id, onDismiss, children }: { id: string; onDismiss: () => void; children: React.ReactNode }) {
  useEffect(() => {
    const timer = setTimeout(onDismiss, 6000);
    return () => clearTimeout(timer);
  }, [id, onDismiss]);
  return <>{children}</>;
}
