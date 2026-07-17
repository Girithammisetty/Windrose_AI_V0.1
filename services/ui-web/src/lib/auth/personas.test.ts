import { describe, it, expect } from "vitest";
import { resolveLogin } from "./personas";

const PERSONAS = JSON.stringify({
  "manager@demo.windrose": {
    sub: "user-manager",
    tenantId: "t-real",
    workspaceId: "ws-real",
    scopes: ["case.case.read"],
  },
});

describe("dev-login persona resolution (fail-closed)", () => {
  it("resolves a known persona email (case-insensitive)", () => {
    const r = resolveLogin("Manager@Demo.Windrose", PERSONAS);
    expect(r.kind).toBe("persona");
    if (r.kind === "persona") {
      expect(r.persona.tenantId).toBe("t-real");
      expect(r.persona.workspaceId).toBe("ws-real");
    }
  });

  it("REJECTS an unknown email when a personas map is configured (no ghost-tenant fallback)", () => {
    expect(resolveLogin("bogus@demo.windrose", PERSONAS)).toEqual({ kind: "unknown-user" });
  });

  it("falls back to dev defaults ONLY when the personas map is entirely absent", () => {
    expect(resolveLogin("anyone@acme.com", undefined)).toEqual({ kind: "dev-default" });
    expect(resolveLogin("anyone@acme.com", "")).toEqual({ kind: "dev-default" });
  });

  it("treats an empty or malformed personas map as absent (self-contained dev)", () => {
    expect(resolveLogin("anyone@acme.com", "{}")).toEqual({ kind: "dev-default" });
    expect(resolveLogin("anyone@acme.com", "not-json")).toEqual({ kind: "dev-default" });
    expect(resolveLogin("anyone@acme.com", "[1,2]")).toEqual({ kind: "dev-default" });
  });
});
