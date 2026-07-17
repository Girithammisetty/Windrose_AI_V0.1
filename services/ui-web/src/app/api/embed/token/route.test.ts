import { describe, it, expect, beforeEach } from "vitest";
import { secretOk, resolveEmbedRequest } from "@/lib/embed/token";

const SECRET = "test-embed-secret";

const validBody = {
  tenantId: "t-1",
  workspaceId: "ws-1",
  sub: "user-embed",
  surface: ["dashboard"],
  resourceId: "dash-42",
  ttlSeconds: 300,
};

beforeEach(() => {
  process.env.WINDROSE_EMBED_SECRET = SECRET;
});

describe("embed token — secret gate", () => {
  it("accepts only the exact configured secret (constant-time)", () => {
    expect(secretOk(SECRET)).toBe(true);
    expect(secretOk("nope")).toBe(false);
    expect(secretOk(null)).toBe(false);
  });

  it("rejects everything when no secret is configured", () => {
    delete process.env.WINDROSE_EMBED_SECRET;
    expect(secretOk(SECRET)).toBe(false);
  });
});

describe("embed token — request resolution (governance)", () => {
  it("resolves a valid dashboard embed to scoped mint params + URL path", () => {
    const r = resolveEmbedRequest(validBody);
    expect("error" in r).toBe(false);
    if ("error" in r) return;
    expect(r.mint).toMatchObject({
      sub: "user-embed",
      tenantId: "t-1",
      workspaceId: "ws-1",
      surface: ["dashboard"],
      ttlSeconds: 300,
    });
    expect(r.path).toBe("/embed/dashboard/dash-42");
  });

  it("defaults narrow read-only scopes when none are given", () => {
    const r = resolveEmbedRequest(validBody);
    if ("error" in r) throw new Error("unexpected");
    expect(r.mint.scopes).toEqual(["chart.dashboard.read"]);
  });

  it("rejects an unknown surface", () => {
    const r = resolveEmbedRequest({ ...validBody, surface: ["everything"] });
    expect(r).toMatchObject({ status: 400 });
  });

  it("requires tenant, workspace and sub", () => {
    expect(resolveEmbedRequest({ surface: ["dashboard"] })).toMatchObject({ status: 400 });
  });

  it("clamps the TTL to the 1h ceiling and 60s floor", () => {
    const hi = resolveEmbedRequest({ ...validBody, ttlSeconds: 99999 });
    const lo = resolveEmbedRequest({ ...validBody, ttlSeconds: 1 });
    if ("error" in hi || "error" in lo) throw new Error("unexpected");
    expect(hi.ttl).toBe(3600);
    expect(lo.ttl).toBe(60);
  });
});
