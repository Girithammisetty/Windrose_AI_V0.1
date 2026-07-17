"use client";
/**
 * Subscribe a screen to a set of realtime-hub topics. Events are routed through
 * the EventBridge (dispatchEvent → patchers) into the shared QueryClient; the
 * hook returns nothing to render — status appears through the patched caches
 * (UI-FR-012, no polling). Degradation flips the global "live paused" flag (BR-5),
 * and a reconnect invalidates active queries so nothing shows a stale "running".
 */
import { useEffect, useRef } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { openHubStream, type HubStream } from "./connection";
import { dispatchEvent } from "./patchers";
import { useRealtimeHealth } from "@/stores/ui";

export function useHubTopics(topics: string[], enabled = true) {
  const client = useQueryClient();
  const setDegraded = useRealtimeHealth((s) => s.setDegraded);
  const streamRef = useRef<HubStream | null>(null);
  const key = topics.slice().sort().join("|");

  useEffect(() => {
    if (!enabled || topics.length === 0) return;
    if (typeof window === "undefined" || typeof EventSource === "undefined") return;

    const stream = openHubStream({
      topics,
      handlers: {
        onEvent: (topic, data) => dispatchEvent(client, { topic, data }),
        onState: (state) => setDegraded(state === "degraded"),
        onReconnect: () => {
          // Recovery guard: refetch everything active so no stale frame lingers.
          void client.invalidateQueries();
          setDegraded(false);
        },
      },
    });
    streamRef.current = stream;
    return () => {
      stream.close();
      streamRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, enabled]);
}
