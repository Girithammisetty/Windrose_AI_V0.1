"use client";
import { AlertTriangle, Inbox, Lock, Search } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/primitives";
import { GraphQLRequestError } from "@/lib/graphql/client";
import { t } from "@/lib/i18n/messages";

/** Master error-code → user message map (UI-FR-014). */
const ERROR_COPY: Record<string, string> = {
  PERMISSION_DENIED: t("state.permissionDenied"),
  UNAUTHENTICATED: "Your session expired. Please sign in again.",
  NOT_FOUND: t("state.notFound"),
  VALIDATION_FAILED: "The request was invalid.",
  UNAVAILABLE: "A service is temporarily unavailable.",
  QUERY_TOO_COMPLEX: "That view is too large to load at once.",
  INTERNAL: t("state.error"),
};

export interface AsyncBoundaryProps {
  isLoading: boolean;
  isError: boolean;
  error?: unknown;
  isEmpty?: boolean;
  emptyTitle?: string;
  emptyCta?: React.ReactNode;
  onRetry?: () => void;
  skeleton?: React.ReactNode;
  children: React.ReactNode;
}

/**
 * Standard async triad (UI-FR-014): skeleton / typed error panel / empty state.
 * The error panel maps master codes to friendly copy and always surfaces the
 * trace_id (BR-10). PERMISSION_DENIED renders a non-leaking access state.
 */
export function AsyncBoundary({
  isLoading,
  isError,
  error,
  isEmpty,
  emptyTitle,
  emptyCta,
  onRetry,
  skeleton,
  children,
}: AsyncBoundaryProps) {
  if (isLoading) {
    return <>{skeleton ?? <DefaultSkeleton />}</>;
  }
  if (isError) {
    const gql = error instanceof GraphQLRequestError ? error : null;
    const code = gql?.code ?? "INTERNAL";
    const isPerm = code === "PERMISSION_DENIED";
    return (
      <div
        role="alert"
        className="flex flex-col items-center justify-center gap-3 rounded-lg border border-dashed p-8 text-center"
      >
        {isPerm ? (
          <Lock className="size-8 text-muted-foreground" aria-hidden />
        ) : (
          <AlertTriangle className="size-8 text-destructive" aria-hidden />
        )}
        <div>
          <p className="font-medium">{ERROR_COPY[code] ?? t("state.error")}</p>
          {gql?.message && !isPerm && <p className="mt-1 text-sm text-muted-foreground">{gql.message}</p>}
          {gql?.traceId && (
            <p className="mt-2 font-mono text-xs text-muted-foreground" data-trace-id={gql.traceId}>
              trace: {gql.traceId}
            </p>
          )}
        </div>
        {!isPerm && onRetry && (
          <Button variant="outline" size="sm" onClick={onRetry}>
            {t("action.retry")}
          </Button>
        )}
        {gql?.traceId && (
          <Button
            variant="ghost"
            size="sm"
            onClick={() =>
              navigator.clipboard?.writeText(
                `route:${typeof window !== "undefined" ? window.location.pathname : ""} trace:${gql.traceId} code:${code}`,
              )
            }
          >
            {t("action.reportIssue")}
          </Button>
        )}
      </div>
    );
  }
  if (isEmpty) {
    return (
      <div className="flex flex-col items-center justify-center gap-3 rounded-lg border border-dashed p-10 text-center">
        <Inbox className="size-8 text-muted-foreground" aria-hidden />
        <p className="font-medium">{emptyTitle ?? t("state.empty")}</p>
        {emptyCta}
      </div>
    );
  }
  return <>{children}</>;
}

function DefaultSkeleton() {
  return (
    <div className="space-y-2" aria-busy="true" aria-label={t("state.loading")}>
      {Array.from({ length: 6 }).map((_, i) => (
        <Skeleton key={i} className="h-10 w-full" />
      ))}
    </div>
  );
}

export function SearchEmpty({ label }: { label?: string }) {
  return (
    <div className="flex flex-col items-center gap-2 py-10 text-center text-muted-foreground">
      <Search className="size-6" aria-hidden />
      <p className="text-sm">{label ?? t("state.empty")}</p>
    </div>
  );
}
