import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook, act } from "@testing-library/react";

/** The hub stream is a boundary (EventSource) — doubled here; the assertion
 * target is the REAL request body posted to /api/copilot/message. */
const streamCloses: ReturnType<typeof vi.fn>[] = [];
vi.mock("@/lib/realtime/connection", () => ({
  openHubStream: vi.fn(() => {
    const close = vi.fn();
    streamCloses.push(close);
    return { close };
  }),
}));

import { useCopilotThread } from "./useCopilotThread";

const fetchCalls: { url: string; body: any }[] = [];

beforeEach(() => {
  fetchCalls.length = 0;
  streamCloses.length = 0;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init: any) => {
      const body = JSON.parse(init.body);
      fetchCalls.push({ url, body });
      return new Response(
        JSON.stringify({ threadId: "th-1", runId: "run-1", sessionId: `sess-${body.agentKey ?? "default"}`, topics: ["agent_run:run-1"] }),
        { status: 200, headers: { "content-type": "application/json" } },
      );
    }),
  );
});

describe("useCopilotThread agentKey routing (Tier 2b)", () => {
  it("posts the module specialist agentKey and replays the returned sessionId", async () => {
    const { result } = renderHook(() => useCopilotThread("wr:t:workspace:ws", "model-training"));

    await act(async () => {
      await result.current.send("train a severity model");
    });
    expect(fetchCalls[0].url).toBe("/api/copilot/message");
    expect(fetchCalls[0].body.agentKey).toBe("model-training");
    expect(fetchCalls[0].body.sessionId).toBeNull(); // first turn: no session yet
    expect(fetchCalls[0].body.contextUrn).toBe("wr:t:workspace:ws");

    await act(async () => {
      await result.current.send("use the claims dataset");
    });
    // Thread continuity: the agent-runtime session from turn 1 rides on turn 2.
    expect(fetchCalls[1].body.agentKey).toBe("model-training");
    expect(fetchCalls[1].body.sessionId).toBe("sess-model-training");
  });

  it("sends agentKey null (default agent) when no specialist applies", async () => {
    const { result } = renderHook(() => useCopilotThread("wr:t:workspace:ws"));
    await act(async () => {
      await result.current.send("hello");
    });
    expect(fetchCalls[0].body.agentKey).toBeNull();
  });

  it("closes the live SSE stream on unmount (no leak)", async () => {
    const { result, unmount } = renderHook(() => useCopilotThread("wr:t:workspace:ws"));
    await act(async () => {
      await result.current.send("stream something");
    });
    // A hub stream is open and not yet closed while the thread is mounted.
    expect(streamCloses).toHaveLength(1);
    expect(streamCloses[0]).not.toHaveBeenCalled();
    // Unmounting (drawer close via navigation) must tear the stream down.
    unmount();
    expect(streamCloses[0]).toHaveBeenCalled();
  });
});
