"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { openHubStream, type HubStream } from "@/lib/realtime/connection";

/** Bound the retained thread so a very long-lived conversation can't grow the
 * message array (and its per-render re-scroll/re-map cost) without limit. */
const MAX_RETAINED_MESSAGES = 200;

export interface Citation {
  urn: string;
  label?: string;
}
export interface SuggestedAction {
  label: string;
  /** A suggested write always routes to a proposal (BR-13) — never a mutation. */
  proposalId?: string;
  href?: string;
}
export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  text: string;
  streaming?: boolean;
  citations?: Citation[];
  actions?: SuggestedAction[];
}

/**
 * Copilot thread state + real SSE token streaming. Sending posts to
 * /api/copilot/message (→ agent-runtime) and subscribes to the returned hub
 * topics; assistant tokens stream in via realtime-hub (no polling). The context
 * URN is sent with the first message (AC-3).
 *
 * `agentKey` (Tier 2b) selects a module specialist (onboarding /
 * model-training / inference / dashboard-designer, …) — it is forwarded to the
 * API route, which allowlists it against agent-runtime's published catalog;
 * null/undefined keeps the default copilot agent. The agent-runtime session id
 * returned by the first turn is replayed on subsequent turns for thread
 * continuity, keyed per agent (each specialist runs its own session).
 */
export function useCopilotThread(contextUrn: string | null, agentKey?: string | null) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [streaming, setStreaming] = useState(false);
  const threadRef = useRef<string | null>(null);
  // agent-runtime session per agent key ("" = default agent).
  const sessionsRef = useRef<Map<string, string>>(new Map());
  const streamRef = useRef<HubStream | null>(null);
  const noResponseTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Tear down the live SSE stream and any pending timer when the component
  // unmounts (drawer close via navigation, route change). Without this an
  // in-flight stream + its jittered reconnect timers leak, and the
  // no-response timer would fire setMessages on an unmounted thread.
  useEffect(() => {
    return () => {
      streamRef.current?.close();
      streamRef.current = null;
      if (noResponseTimerRef.current) clearTimeout(noResponseTimerRef.current);
    };
  }, []);

  const send = useCallback(
    async (text: string) => {
      const userMsg: ChatMessage = { id: crypto.randomUUID(), role: "user", text };
      const assistantId = crypto.randomUUID();
      const assistantMsg: ChatMessage = { id: assistantId, role: "assistant", text: "", streaming: true };
      setMessages((m) => [...m, userMsg, assistantMsg].slice(-MAX_RETAINED_MESSAGES));
      setStreaming(true);

      const sessionKey = agentKey ?? "";
      let res: Response;
      try {
        res = await fetch("/api/copilot/message", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({
            threadId: threadRef.current,
            text,
            contextUrn,
            agentKey: agentKey ?? null,
            sessionId: sessionsRef.current.get(sessionKey) ?? null,
          }),
        });
      } catch {
        finishWithError(assistantId, "The copilot is unreachable.");
        return;
      }
      if (!res.ok) {
        finishWithError(assistantId, "The copilot is unavailable right now.");
        return;
      }
      const { threadId, topics, sessionId } = (await res.json()) as {
        threadId: string;
        topics: string[];
        sessionId?: string | null;
      };
      threadRef.current = threadId;
      if (sessionId) sessionsRef.current.set(sessionKey, sessionId);

      streamRef.current?.close();
      // The subscription may connect but receive no tokens (e.g. the agent
      // produced no streamed output). Don't spin forever — surface an honest
      // message if nothing arrives. Cleared on the first token or a done event.
      let sawEvent = false;
      if (noResponseTimerRef.current) clearTimeout(noResponseTimerRef.current);
      const noResponseTimer = setTimeout(() => {
        if (!sawEvent) finishWithError(assistantId, "The copilot didn't return a response.");
      }, 20000);
      noResponseTimerRef.current = noResponseTimer;
      streamRef.current = openHubStream({
        topics,
        handlers: {
          onEvent: (topic, data) => {
            // realtime-hub emits `event: <topic>`, so the token/citation/action/
            // done semantic rides either in the topic name or the payload `type`.
            const kind = (typeof data === "object" && data?.type) || topic;
            if (String(kind).includes("token")) {
              sawEvent = true;
              clearTimeout(noResponseTimer);
              const chunk = typeof data === "string" ? data : (data?.text ?? data?.delta ?? "");
              setMessages((m) =>
                m.map((msg) => (msg.id === assistantId ? { ...msg, text: msg.text + chunk } : msg)),
              );
            } else if (String(kind).includes("citation") && data?.urn) {
              setMessages((m) =>
                m.map((msg) =>
                  msg.id === assistantId
                    ? { ...msg, citations: [...(msg.citations ?? []), { urn: data.urn, label: data.label }] }
                    : msg,
                ),
              );
            } else if (String(kind).includes("action")) {
              setMessages((m) =>
                m.map((msg) =>
                  msg.id === assistantId
                    ? {
                        ...msg,
                        actions: [
                          ...(msg.actions ?? []),
                          { label: data?.label ?? "Review proposal", proposalId: data?.proposalId, href: data?.href },
                        ],
                      }
                    : msg,
                ),
              );
            } else if (String(kind).includes("done")) {
              sawEvent = true;
              clearTimeout(noResponseTimer);
              setMessages((m) => m.map((msg) => (msg.id === assistantId ? { ...msg, streaming: false } : msg)));
              setStreaming(false);
              streamRef.current?.close();
              streamRef.current = null;
            }
          },
        },
      });
    },
    [contextUrn, agentKey],
  );

  function finishWithError(assistantId: string, text: string) {
    setMessages((m) => m.map((msg) => (msg.id === assistantId ? { ...msg, text, streaming: false } : msg)));
    setStreaming(false);
  }

  const reset = useCallback(() => {
    streamRef.current?.close();
    streamRef.current = null;
    threadRef.current = null;
    sessionsRef.current.clear();
    setMessages([]);
    setStreaming(false);
  }, []);

  return { messages, streaming, send, reset };
}
