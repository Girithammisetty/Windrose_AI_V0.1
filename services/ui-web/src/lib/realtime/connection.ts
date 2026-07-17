"use client";
/**
 * Real SSE transport to realtime-hub. Mints a single-use ticket via the
 * same-origin /api/rt/ticket route (which carries the user's Bearer), then opens
 * an EventSource to {hubUrl}/api/v1/stream?ticket=... — a direct browser→hub SSE
 * connection, never proxied (UI-FR-003 / RTH-FR-011). No polling anywhere.
 *
 * State machine (§4): connecting → open → degraded(retry backoff 1s→30s jittered)
 * → open. On (re)connect the caller is asked to invalidate active queries (BR-5).
 */

export type ConnState = "connecting" | "open" | "degraded" | "closed";

export interface HubStreamHandlers {
  onEvent: (topic: string, data: any, eventId?: string) => void;
  onState?: (state: ConnState) => void;
  /** Fired after a successful (re)connect so the caller can invalidate queries. */
  onReconnect?: () => void;
}

export interface HubStream {
  close: () => void;
  state: () => ConnState;
}

const DEGRADED_AFTER_MS = 60_000; // BR-5: show "live paused" after 60s down.

interface OpenOpts {
  topics: string[];
  handlers: HubStreamHandlers;
  /** Injectable for tests; defaults to window.EventSource. */
  eventSourceFactory?: (url: string) => EventSourceLike;
  mintTicket?: (topics: string[]) => Promise<{ hubUrl: string; ticket: string }>;
}

export interface EventSourceLike {
  addEventListener: (type: string, cb: (ev: MessageEvent) => void) => void;
  close: () => void;
  onerror: ((ev: unknown) => void) | null;
  onopen: ((ev: unknown) => void) | null;
}

async function defaultMint(topics: string[]): Promise<{ hubUrl: string; ticket: string }> {
  const res = await fetch("/api/rt/ticket", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ topics }),
  });
  if (!res.ok) throw new Error(`ticket mint failed: ${res.status}`);
  return (await res.json()) as { hubUrl: string; ticket: string };
}

/**
 * Opens a resilient hub stream. Returns a controller; call close() to release.
 * Reconnects with jittered exponential backoff and flips to `degraded` when the
 * outage exceeds 60s. Control frames (connected/heartbeat/close) are handled here;
 * topic frames are forwarded to onEvent.
 */
export function openHubStream(opts: OpenOpts): HubStream {
  const { topics, handlers } = opts;
  const mint = opts.mintTicket ?? defaultMint;
  const factory =
    opts.eventSourceFactory ??
    ((url: string) => new EventSource(url) as unknown as EventSourceLike);

  let es: EventSourceLike | null = null;
  let state: ConnState = "connecting";
  let closed = false;
  let attempt = 0;
  let downSince = 0;
  let degradeTimer: ReturnType<typeof setTimeout> | null = null;
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  let hadConnected = false;

  function setState(s: ConnState) {
    if (state === s) return;
    state = s;
    handlers.onState?.(s);
  }

  function scheduleDegrade() {
    if (degradeTimer) return;
    if (!downSince) downSince = Date.now();
    // Flip to degraded only once the outage crosses the threshold (BR-5).
    degradeTimer = setTimeout(() => {
      if (!closed && state !== "open") setState("degraded");
    }, DEGRADED_AFTER_MS);
  }

  function clearDegrade() {
    if (degradeTimer) {
      clearTimeout(degradeTimer);
      degradeTimer = null;
    }
    downSince = 0;
  }

  async function connect() {
    if (closed) return;
    setState(state === "degraded" ? "degraded" : "connecting");
    try {
      const { hubUrl, ticket } = await mint(topics);
      if (closed) return;
      const url = `${hubUrl}/api/v1/stream?ticket=${encodeURIComponent(ticket)}`;
      const source = factory(url);
      es = source;

      source.onopen = () => {
        attempt = 0;
        clearDegrade();
        setState("open");
        if (hadConnected) handlers.onReconnect?.();
        hadConnected = true;
      };

      source.addEventListener("control", (ev) => {
        try {
          const payload = JSON.parse((ev as MessageEvent).data);
          if (payload?.type === "connected") {
            attempt = 0;
            clearDegrade();
            setState("open");
            if (hadConnected) handlers.onReconnect?.();
            hadConnected = true;
          } else if (payload?.type === "close") {
            reconnect();
          }
          // heartbeat: keepalive, no-op.
        } catch {
          /* ignore malformed control frame */
        }
      });

      // Topic frames arrive as named events (event: <topic>).
      for (const topic of topics) {
        source.addEventListener(topic, (ev) => {
          const m = ev as MessageEvent;
          let data: any = m.data;
          try {
            data = JSON.parse(m.data);
          } catch {
            /* pass through raw */
          }
          handlers.onEvent(topic, data, m.lastEventId);
        });
      }
      // Generic message fallback (hubs that don't name the event).
      source.addEventListener("message", (ev) => {
        const m = ev as MessageEvent;
        try {
          const parsed = JSON.parse(m.data);
          if (parsed?.topic) handlers.onEvent(parsed.topic, parsed.data ?? parsed, m.lastEventId);
        } catch {
          /* ignore */
        }
      });

      source.onerror = () => {
        if (closed) return;
        scheduleDegrade();
        reconnect();
      };
    } catch {
      if (closed) return;
      scheduleDegrade();
      reconnect();
    }
  }

  function reconnect() {
    if (closed) return;
    es?.close();
    es = null;
    if (reconnectTimer) return;
    attempt++;
    const base = Math.min(30_000, 1000 * 2 ** Math.min(attempt, 5));
    const jitter = Math.random() * 0.3 * base;
    reconnectTimer = setTimeout(() => {
      reconnectTimer = null;
      void connect();
    }, base + jitter);
  }

  void connect();

  return {
    close() {
      closed = true;
      clearDegrade();
      if (reconnectTimer) clearTimeout(reconnectTimer);
      es?.close();
      setState("closed");
    },
    state: () => state,
  };
}
