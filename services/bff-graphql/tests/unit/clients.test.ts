import { describe, it, expect } from "vitest";
import { ServiceClient } from "../../src/clients/base.js";
import { DownstreamError } from "../../src/errors/errors.js";
import { mockFetch } from "../helpers/mockFetch.js";

describe("ServiceClient (real fetch, JWT passthrough)", () => {
  it("forwards Authorization + trace headers verbatim on every call (BFF-FR-010/012)", async () => {
    const { fetchImpl, requests } = mockFetch(() => ({ status: 200, body: { data: { id: "1" } } }));
    const client = new ServiceClient({
      service: "case-service",
      baseUrl: "http://svc.local",
      ctx: { authorization: "Bearer THE-USER-TOKEN", traceparent: "00-abc-def-01", traceId: "trace-9" },
      fetchImpl,
    });
    await client.get("/api/v1/cases/1");
    expect(requests).toHaveLength(1);
    expect(requests[0]!.headers["authorization"]).toBe("Bearer THE-USER-TOKEN");
    expect(requests[0]!.headers["traceparent"]).toBe("00-abc-def-01");
    expect(requests[0]!.headers["x-trace-id"]).toBe("trace-9");
  });

  it("forwards Idempotency-Key on side-effecting POSTs (MASTER-FR-025)", async () => {
    const { fetchImpl, requests } = mockFetch(() => ({ status: 200, body: {} }));
    const client = new ServiceClient({
      service: "case-service",
      baseUrl: "http://svc.local",
      ctx: { authorization: "Bearer t" },
      fetchImpl,
    });
    await client.post("/api/v1/cases/1/decide", { body: { a: 1 }, idempotencyKey: "idem-42" });
    expect(requests[0]!.headers["idempotency-key"]).toBe("idem-42");
  });

  it("parses the master error envelope into a DownstreamError", async () => {
    const { fetchImpl } = mockFetch(() => ({
      status: 403,
      body: { error: { code: "PERMISSION_DENIED", message: "denied", trace_id: "tr-1" } },
    }));
    const client = new ServiceClient({
      service: "case-service",
      baseUrl: "http://svc.local",
      ctx: {},
      fetchImpl,
    });
    await expect(client.get("/x")).rejects.toMatchObject({
      httpStatus: 403,
      downstreamCode: "PERMISSION_DENIED",
      traceId: "tr-1",
    } satisfies Partial<DownstreamError>);
  });

  it("maps a transport failure to a status-0 DownstreamError (-> SERVICE_UNAVAILABLE)", async () => {
    const fetchImpl = (async () => {
      throw new Error("ECONNREFUSED");
    }) as unknown as typeof fetch;
    const client = new ServiceClient({ service: "dataset-service", baseUrl: "http://svc.local", ctx: {}, fetchImpl });
    await expect(client.get("/x")).rejects.toMatchObject({ httpStatus: 0, service: "dataset-service" });
  });
});
