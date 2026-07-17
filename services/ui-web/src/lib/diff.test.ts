import { describe, it, expect } from "vitest";
import { diffJson, normalizeArgsDiff, changedLeaves } from "./diff";

describe("diffJson", () => {
  it("detects added, removed, changed, unchanged leaves", () => {
    const leaves = diffJson({ a: 1, b: 2, keep: "x" }, { a: 1, b: 3, c: 4, keep: "x" });
    const byPath = Object.fromEntries(leaves.map((l) => [l.path, l.kind]));
    expect(byPath.a).toBe("unchanged");
    expect(byPath.b).toBe("changed");
    expect(byPath.c).toBe("added");
    expect(byPath.keep).toBe("unchanged");
  });

  it("recurses into nested objects with dotted paths", () => {
    const leaves = diffJson({ x: { y: 1 } }, { x: { y: 2 } });
    expect(leaves.find((l) => l.path === "x.y")?.kind).toBe("changed");
  });

  it("treats arrays as atomic values by stable serialization", () => {
    expect(diffJson({ a: [1, 2] }, { a: [1, 2] })[0].kind).toBe("unchanged");
    expect(diffJson({ a: [1, 2] }, { a: [2, 1] })[0].kind).toBe("changed");
  });
});

describe("normalizeArgsDiff", () => {
  it("handles {before,after}", () => {
    expect(normalizeArgsDiff({ before: { a: 1 }, after: { a: 2 } })).toEqual({ before: { a: 1 }, after: { a: 2 } });
  });
  it("handles {current,proposed}", () => {
    expect(normalizeArgsDiff({ current: { a: 1 }, proposed: { a: 2 } })).toEqual({ before: { a: 1 }, after: { a: 2 } });
  });
  it("treats a bare object as the proposed (after) args", () => {
    expect(normalizeArgsDiff({ severity: "HIGH" })).toEqual({ before: {}, after: { severity: "HIGH" } });
  });
});

describe("changedLeaves", () => {
  it("returns only non-unchanged leaves", () => {
    const leaves = changedLeaves({ before: { a: 1, b: 2 }, after: { a: 1, b: 9 } });
    expect(leaves).toHaveLength(1);
    expect(leaves[0].path).toBe("b");
  });
});
