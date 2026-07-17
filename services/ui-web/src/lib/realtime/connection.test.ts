import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { openHubStream, type EventSourceLike, type ConnState } from "./connection";

class FakeES implements EventSourceLike {
  onerror: ((ev: unknown) => void) | null = null;
  onopen: ((ev: unknown) => void) | null = null;
  listeners: Record<string, ((ev: MessageEvent) => void)[]> = {};
  closed = false;
  addEventListener(type: string, cb: (ev: MessageEvent) => void) {
    (this.listeners[type] ??= []).push(cb);
  }
  emit(type: string, data: unknown) {
    for (const cb of this.listeners[type] ?? []) cb({ data: JSON.stringify(data), lastEventId: "1" } as MessageEvent);
  }
  close() {
    this.closed = true;
  }
}

describe("openHubStream state machine (BR-5, AC-15)", () => {
  let sources: FakeES[];
  beforeEach(() => {
    sources = [];
    vi.useFakeTimers();
  });
  afterEach(() => vi.useRealTimers());

  function open(onState: (s: ConnState) => void, onReconnect?: () => void) {
    return openHubStream({
      topics: ["case.status"],
      handlers: { onEvent: () => {}, onState, onReconnect },
      mintTicket: async () => ({ hubUrl: "http://hub", ticket: "tk" }),
      eventSourceFactory: () => {
        const es = new FakeES();
        sources.push(es);
        return es;
      },
    });
  }

  it("goes connecting → open on the control 'connected' frame", async () => {
    const states: ConnState[] = [];
    open((s) => states.push(s));
    await vi.runOnlyPendingTimersAsync(); // resolve mint()
    sources[0].emit("control", { type: "connected", conn_id: "c1" });
    expect(states).toContain("open");
  });

  it("flips to degraded only after a 60s outage, then recovers with onReconnect", async () => {
    const states: ConnState[] = [];
    const reconnects = vi.fn();
    open((s) => states.push(s), reconnects);
    await vi.runOnlyPendingTimersAsync();
    sources[0].emit("control", { type: "connected", conn_id: "c1" });
    expect(states).toContain("open");

    // Connection drops.
    sources[0].onerror?.({});
    // Before 60s: not yet degraded.
    await vi.advanceTimersByTimeAsync(59_000);
    expect(states.filter((s) => s === "degraded")).toHaveLength(0);
    // After 60s: degraded (BR-5).
    await vi.advanceTimersByTimeAsync(2_000);
    expect(states).toContain("degraded");

    // Reconnect fires and a fresh source connects → onReconnect invoked (AC-15).
    await vi.advanceTimersByTimeAsync(60_000);
    const latest = sources[sources.length - 1];
    latest.emit("control", { type: "connected", conn_id: "c2" });
    expect(reconnects).toHaveBeenCalled();
  });
});
