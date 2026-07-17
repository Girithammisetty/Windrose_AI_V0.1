"use client";
import { useMemo } from "react";
import { useMe } from "@/lib/graphql/hooks";
import {
  allows,
  toCapabilitySet,
  EMPTY_CAPABILITIES,
  type CapabilitySet,
  type Gate,
} from "./registry";

export interface Capabilities {
  /** The resolved capability set (fail-safe empty until the viewer loads). */
  set: CapabilitySet;
  /** True while the viewer's roles/capabilities are still loading. */
  isLoading: boolean;
  /** True when the viewer query errored (treated as no capabilities). */
  isError: boolean;
  /** True once the viewer's capabilities are known (loaded, not errored). */
  isReady: boolean;
  /** Whether the viewer satisfies a gate (admin passes everything). */
  can: (gate: Gate) => boolean;
  roles: string[];
  capabilities: string[];
  isAdmin: boolean;
  /** True when the backend's rbac lookup failed and the (empty) capabilities are
   * a fail-closed fallback: the nav stays fail-closed but the shell shows a
   * "permissions unavailable" notice instead of a silently empty menu. */
  capsDegraded: boolean;
}

/**
 * Reads the viewer's roles + capabilities (one cached ME query, shared across
 * every consumer via TanStack) and exposes gate checks. This is the single UI
 * gate: nav, route guard and in-page controls all consult it. Fail-safe — until
 * the capabilities are known, or if the query errors, nothing gated is allowed
 * (public gates still pass). The services enforce regardless.
 */
export function useCapabilities(): Capabilities {
  const me = useMe();
  const viewer = me.data?.me;

  const set = useMemo<CapabilitySet>(
    () =>
      viewer
        ? toCapabilitySet({ capabilities: viewer.capabilities, roles: viewer.roles })
        : EMPTY_CAPABILITIES,
    [viewer],
  );

  return useMemo<Capabilities>(
    () => ({
      set,
      isLoading: me.isLoading,
      isError: me.isError,
      isReady: !!viewer && !me.isError,
      can: (gate: Gate) => allows(gate, set),
      roles: viewer?.roles ?? [],
      capabilities: viewer?.capabilities ?? [],
      isAdmin: set.isAdmin,
      capsDegraded: viewer?.capsDegraded ?? false,
    }),
    [set, me.isLoading, me.isError, viewer],
  );
}
