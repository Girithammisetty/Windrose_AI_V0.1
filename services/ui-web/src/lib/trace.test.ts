import { describe, it, expect } from "vitest";
import { flattenTrace, pathToSpan, isErrorStatus } from "./trace";

const trace = {
  id: "root",
  name: "triage-agent",
  type: "agent",
  status: "ok",
  children: [
    { id: "s1", name: "plan", type: "step", status: "ok" },
    {
      id: "s2",
      name: "tool: search",
      type: "tool_call",
      status: "ok",
      children: [{ id: "s2a", name: "sub", type: "sub_agent", status: "error", error: "boom" }],
    },
  ],
};

describe("flattenTrace", () => {
  it("emits only roots when nothing is expanded (and no errors)", () => {
    const ok = { id: "r", name: "a", type: "agent", status: "ok", children: [{ id: "c", name: "c", status: "ok" }] };
    const rows = flattenTrace(ok, new Set());
    expect(rows).toHaveLength(1);
    expect(rows[0].hasChildren).toBe(true);
  });

  it("expands a node when its id is in the expanded set", () => {
    const rows = flattenTrace({ id: "r", name: "a", type: "agent", status: "ok", children: [{ id: "c", name: "c", status: "ok" }] }, new Set(["r"]));
    expect(rows.map((r) => r.id)).toEqual(["r", "c"]);
  });

  it("auto-expands ancestors of an error node (AC-7)", () => {
    const rows = flattenTrace(trace, new Set());
    const ids = rows.map((r) => r.id);
    // root → s2 → s2a must all be present because s2a errored, even though
    // nothing was manually expanded.
    expect(ids).toContain("s2");
    expect(ids).toContain("s2a");
    expect(rows.find((r) => r.id === "s2a")?.isError).toBe(true);
  });

  it("scales to 800 nodes", () => {
    const big = { id: "root", name: "r", type: "agent", status: "ok", children: Array.from({ length: 800 }, (_, i) => ({ id: `n${i}`, name: `n${i}`, status: "ok" })) };
    const rows = flattenTrace(big, new Set(["root"]));
    expect(rows).toHaveLength(801);
  });
});

describe("pathToSpan", () => {
  it("returns the ancestor ids needed to reveal a span", () => {
    expect(pathToSpan(trace, "s2a")).toEqual(["root", "s2"]);
  });
});

describe("isErrorStatus", () => {
  it("recognizes error-like statuses", () => {
    expect(isErrorStatus("error")).toBe(true);
    expect(isErrorStatus("FAILED")).toBe(true);
    expect(isErrorStatus("ok")).toBe(false);
  });
});
