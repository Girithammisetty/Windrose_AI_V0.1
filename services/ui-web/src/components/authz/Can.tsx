"use client";
import { useCapabilities } from "@/lib/authz/useCapabilities";
import type { Gate } from "@/lib/authz/registry";

/**
 * Renders `children` only when the viewer satisfies `gate` (UX gate for in-page
 * controls — buttons, menu items, cards). Fail-safe: hidden until the viewer's
 * capabilities are known and while they are absent. The server still enforces.
 */
export function Can({
  gate,
  children,
  fallback = null,
}: {
  gate: Gate;
  children: React.ReactNode;
  fallback?: React.ReactNode;
}) {
  const { can } = useCapabilities();
  return <>{can(gate) ? children : fallback}</>;
}
