/**
 * Real integration test: boots the actual bff-graphql HTTP server (BRD 58
 * SEC-3) and drives it over real HTTP to prove the CORS allowlist and static
 * security headers are genuinely applied by the raw http.Server request
 * handler -- not just present in isolated helper-function unit tests.
 */
import { describe, it, expect, beforeAll, afterAll } from "vitest";
import type { AddressInfo } from "node:net";
import type http from "node:http";

describe("CORS allowlist + security headers", () => {
  let bff: http.Server;
  let bffUrl: string;

  beforeAll(async () => {
    process.env.NODE_ENV = "test";
    process.env.VERIFY_JWT = "false";
    process.env.CORS_ALLOWED_ORIGINS = "http://localhost:3000,https://tenant.example.com";
    process.env.PORT = "0";
    const { main } = await import("../../src/index.js");
    bff = await main();
    bffUrl = `http://127.0.0.1:${(bff.address() as AddressInfo).port}`;
  }, 30_000);

  afterAll(async () => {
    await new Promise<void>((r) => bff.close(() => r()));
  });

  it("reflects an allowlisted origin and answers the preflight without reaching GraphQL", async () => {
    const res = await fetch(`${bffUrl}/graphql`, {
      method: "OPTIONS",
      headers: { origin: "http://localhost:3000" },
    });
    expect(res.status).toBe(204);
    expect(res.headers.get("access-control-allow-origin")).toBe("http://localhost:3000");
    expect(res.headers.get("access-control-allow-methods")).toContain("POST");
    expect(res.headers.get("access-control-allow-headers")).toContain("authorization");
  });

  it("reflects a second configured origin too (not just the first)", async () => {
    const res = await fetch(`${bffUrl}/healthz`, {
      headers: { origin: "https://tenant.example.com" },
    });
    expect(res.headers.get("access-control-allow-origin")).toBe("https://tenant.example.com");
  });

  it("never sets an Allow-Origin header for an origin that is not on the allowlist", async () => {
    const res = await fetch(`${bffUrl}/healthz`, {
      headers: { origin: "https://evil.example.com" },
    });
    expect(res.status).toBe(200); // request still served -- CORS is enforced by the browser, not a 403
    expect(res.headers.get("access-control-allow-origin")).toBeNull();
  });

  it("never sets a wildcard Allow-Origin, even implicitly", async () => {
    const res = await fetch(`${bffUrl}/healthz`, { headers: { origin: "http://localhost:3000" } });
    expect(res.headers.get("access-control-allow-origin")).not.toBe("*");
  });

  it("sets X-Content-Type-Options and X-Frame-Options on every response", async () => {
    const res = await fetch(`${bffUrl}/healthz`);
    expect(res.headers.get("x-content-type-options")).toBe("nosniff");
    expect(res.headers.get("x-frame-options")).toBe("DENY");
  });
});
