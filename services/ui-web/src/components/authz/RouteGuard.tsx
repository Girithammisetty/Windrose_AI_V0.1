"use client";
import { usePathname } from "next/navigation";
import { useCapabilities } from "@/lib/authz/useCapabilities";
import { gateForPath } from "@/lib/authz/registry";
import { Skeleton } from "@/components/ui/primitives";
import { NoAccess } from "./NoAccess";

/**
 * Client route guard (UI-FR-004). Resolves the current route's required
 * capability from the registry and renders a non-leaking NoAccess state instead
 * of the page when the viewer lacks it — so navigating (or deep-linking) to a
 * route the persona can't use never shows that page's content. Public routes
 * render immediately. While capabilities load, a skeleton avoids a denied flash;
 * fail-safe otherwise (unknown/absent capability blocks the page). The server
 * still enforces every underlying operation.
 */
export function RouteGuard({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const gate = gateForPath(pathname);
  const { can, isLoading } = useCapabilities();

  if (gate.kind === "public") return <>{children}</>;
  // Still loading: show a skeleton rather than a denied flash. On error the
  // query settles with an empty capability set, so the check below fails safe.
  if (isLoading) {
    return (
      <div className="space-y-3" aria-busy="true">
        <Skeleton className="h-8 w-48" />
        <Skeleton className="h-40 w-full" />
      </div>
    );
  }
  if (!can(gate)) return <NoAccess />;
  return <>{children}</>;
}
