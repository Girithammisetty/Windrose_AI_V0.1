import { describe, it, expect } from "vitest";
import { toLimitCursor, toConnection } from "../../src/pagination.js";
import { loadConfig } from "../../src/config.js";
import { ErrorCode } from "../../src/errors/errors.js";

const limits = loadConfig().limits;

describe("cursor pagination (BFF-FR-020 / AC-13)", () => {
  it("maps first/after to limit/cursor with defaults", () => {
    expect(toLimitCursor({}, limits)).toEqual({ limit: 50, cursor: undefined });
    expect(toLimitCursor({ first: 10, after: "c1" }, limits)).toEqual({ limit: 10, cursor: "c1" });
  });

  it("rejects first > 200 with VALIDATION_FAILED (mirrors REST cap)", () => {
    try {
      toLimitCursor({ first: 250 }, limits);
      throw new Error("should have thrown");
    } catch (e: any) {
      expect(e.extensions.code).toBe(ErrorCode.VALIDATION_FAILED);
    }
  });

  it("wraps a REST page envelope into a Connection", () => {
    const conn = toConnection(
      { data: [{ id: "a" }, { id: "b" }], page: { next_cursor: "n1", has_more: true } },
      (x) => ({ id: x.id }),
    );
    expect(conn.nodes.map((n) => n.id)).toEqual(["a", "b"]);
    expect(conn.pageInfo).toEqual({ nextCursor: "n1", hasMore: true });
  });
});
