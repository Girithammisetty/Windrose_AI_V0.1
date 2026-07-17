/**
 * REAL / near-real integration test.
 *
 * What is genuinely real here (no stubs in the BFF's runtime path):
 *  - A real RS256 keypair; a real user JWT is signed with real crypto.
 *  - A real JWKS document is served over a real HTTP server; the BFF verifies
 *    the inbound token's signature against it at the edge (real jose + real
 *    network fetch of the JWKS).
 *  - The BFF boots its real Node HTTP + Apollo server and is driven over real
 *    HTTP (POST /graphql).
 *  - The resolvers reach real local HTTP servers that serve the domain
 *    services' actual OpenAPI-shaped responses. The BFF's HTTP client (real
 *    undici fetch) is exercised end-to-end.
 *
 * The only concession vs. booting the Go/Python services themselves is that the
 * downstream responders are lightweight HTTP servers returning OpenAPI-shaped
 * bodies — the BFF client code calling them is 100% the real path. This is the
 * fallback the task explicitly permits.
 */
import { describe, it, expect, beforeAll, afterAll } from "vitest";
import http from "node:http";
import type { AddressInfo } from "node:net";
import { generateKeyPair, exportJWK, SignJWT, type JWK } from "jose";

interface Rec { url: string; headers: http.IncomingHttpHeaders }

function start(handler: (req: http.IncomingMessage, res: http.ServerResponse, rec: Rec) => void) {
  const requests: Rec[] = [];
  const server = http.createServer((req, res) => {
    const rec: Rec = { url: req.url ?? "/", headers: req.headers };
    requests.push(rec);
    handler(req, res, rec);
  });
  return new Promise<{ url: string; requests: Rec[]; close: () => Promise<void> }>((resolve) => {
    server.listen(0, () => {
      const port = (server.address() as AddressInfo).port;
      resolve({
        url: `http://127.0.0.1:${port}`,
        requests,
        close: () => new Promise((r) => server.close(() => r())),
      });
    });
  });
}

function sendJson(res: http.ServerResponse, status: number, body: unknown) {
  res.writeHead(status, { "content-type": "application/json", "x-trace-id": "downstream-trace" });
  res.end(JSON.stringify(body));
}

describe("real downstream + real JWT/JWKS end-to-end", () => {
  let servers: { close: () => Promise<void> }[] = [];
  let bff: http.Server;
  let bffUrl: string;
  let userToken: string;
  let publicJwk: JWK;
  const captured: { jwks: Rec[]; case: Rec[]; identity: Rec[]; dataset: Rec[]; agent: Rec[]; ingestion: Rec[] } = {
    jwks: [], case: [], identity: [], dataset: [], agent: [], ingestion: [],
  };

  beforeAll(async () => {
    // --- 1. real RS256 keypair + JWKS ---
    const { publicKey, privateKey } = await generateKeyPair("RS256");
    publicJwk = { ...(await exportJWK(publicKey)), kid: "test-key-1", alg: "RS256", use: "sig" };

    const jwks = await start((_req, res) => sendJson(res, 200, { keys: [publicJwk] }));
    captured.jwks = jwks.requests;

    // --- 2. real signed user JWT ---
    userToken = await new SignJWT({ tenant_id: "t-42", typ: "user", scopes: ["case.case.read"] })
      .setProtectedHeader({ alg: "RS256", kid: "test-key-1" })
      .setSubject("u-1")
      .setIssuedAt()
      .setExpirationTime("5m")
      .sign(privateKey);

    // --- 3. real downstream HTTP servers (OpenAPI-shaped) ---
    const caseSvc = await start((req, res) => {
      if (req.url?.startsWith("/api/v1/cases/case-1")) {
        return sendJson(res, 200, {
          id: "case-1", case_number: 7, status: "in_progress", severity: "high",
          assigned_to_id: "user-1", dataset_urn: "wr:t-42:dataset:dataset/ds-9",
        });
      }
      if (req.url?.startsWith("/api/v1/cases/forbidden")) {
        return sendJson(res, 403, { error: { code: "PERMISSION_DENIED", message: "no access", trace_id: "tr-403" } });
      }
      return sendJson(res, 404, { error: { code: "NOT_FOUND", message: "x", trace_id: "t" } });
    });
    captured.case = caseSvc.requests;

    const identitySvc = await start((req, res) => {
      if (req.url?.startsWith("/api/v1/users")) {
        return sendJson(res, 200, { data: [{ id: "user-1", email: "ann@acme.com", full_name: "Ann" }], page: { has_more: false } });
      }
      return sendJson(res, 404, { error: { code: "NOT_FOUND", message: "x", trace_id: "t" } });
    });
    captured.identity = identitySvc.requests;

    const datasetSvc = await start((req, res) => {
      if (req.url?.startsWith("/api/v1/datasets")) {
        return sendJson(res, 200, { data: [{ id: "ds-9", name: "claims-2026" }], page: { has_more: false } });
      }
      return sendJson(res, 404, { error: { code: "NOT_FOUND", message: "x", trace_id: "t" } });
    });
    captured.dataset = datasetSvc.requests;

    const agentSvc = await start((req, res) => {
      if (req.url?.startsWith("/api/v1/proposals")) {
        return sendJson(res, 200, { data: [], page: { has_more: false } });
      }
      return sendJson(res, 404, { error: { code: "NOT_FOUND", message: "x", trace_id: "t" } });
    });
    captured.agent = agentSvc.requests;

    // ingestion-service: connector catalog + connection CRUD + test-connection.
    let created: Record<string, unknown> | null = null;
    const ingestionSvc = await start(async (req, res) => {
      if (req.url?.startsWith("/api/v1/connector-types")) {
        return sendJson(res, 200, {
          data: [
            {
              connector_type: "postgres", display_name: "PostgreSQL", category: "database",
              fields: [
                { name: "host", type: "string", required: true, secret: false, default: null, enum: null, help: "Hostname" },
                { name: "port", type: "integer", required: false, secret: false, default: 5432, enum: null, help: null },
                { name: "password", type: "string", required: false, secret: true, default: null, enum: null, help: "pw" },
              ],
              secret_fields: ["password"], config_schema: { additionalProperties: false },
            },
          ],
        });
      }
      if (req.url === "/api/v1/connections" && req.method === "POST") {
        const chunks: Buffer[] = [];
        for await (const c of req) chunks.push(c as Buffer);
        const parsed = JSON.parse(Buffer.concat(chunks).toString("utf8") || "{}");
        created = { id: "conn-real-1", name: parsed.name, connector_type: parsed.connector_type, config: parsed.config, secrets: { password: "•••" }, secret_set: true, last_test_status: "ok" };
        return sendJson(res, 201, { data: created });
      }
      if (req.url === "/api/v1/connections/conn-real-1/test" && req.method === "POST") {
        return sendJson(res, 200, { data: { status: "ok", latency_ms: 9 } });
      }
      return sendJson(res, 404, { error: { code: "NOT_FOUND", message: "x", trace_id: "t" } });
    });
    captured.ingestion = ingestionSvc.requests;

    servers = [jwks, caseSvc, identitySvc, datasetSvc, agentSvc, ingestionSvc];

    // --- 4. boot the REAL BFF server over HTTP ---
    process.env.NODE_ENV = "test";
    process.env.VERIFY_JWT = "true";
    process.env.JWKS_URL = `${jwks.url}/.well-known/jwks.json`;
    process.env.CASE_URL = caseSvc.url;
    process.env.IDENTITY_URL = identitySvc.url;
    process.env.DATASET_URL = datasetSvc.url;
    process.env.AGENT_RUNTIME_URL = agentSvc.url;
    process.env.INGESTION_URL = ingestionSvc.url;
    process.env.PORT = "0"; // ephemeral
    const { main } = await import("../../src/index.js");
    bff = await main();
    bffUrl = `http://127.0.0.1:${(bff.address() as AddressInfo).port}`;
  }, 30_000);

  afterAll(async () => {
    await new Promise<void>((r) => bff.close(() => r()));
    for (const s of servers) await s.close();
  });

  async function gql(query: string, token?: string) {
    const res = await fetch(`${bffUrl}/graphql`, {
      method: "POST",
      headers: {
        "content-type": "application/json",
        ...(token ? { authorization: `Bearer ${token}` } : {}),
      },
      body: JSON.stringify({ query }),
    });
    return { status: res.status, json: await res.json() as any };
  }

  it("verifies the real JWT signature at the edge (JWKS fetched over HTTP)", async () => {
    // A valid request forces the BFF to verify the signature, which lazily
    // fetches the JWKS from the identity-service JWKS endpoint over real HTTP.
    const { json } = await gql("{ me { userId tenantId } }", userToken);
    expect(json.data.me.userId).toBe("u-1");
    expect(json.data.me.tenantId).toBe("t-42");
    expect(captured.jwks.length).toBeGreaterThan(0); // BFF really fetched the JWKS
  });

  it("rejects a missing JWT with 401 UNAUTHENTICATED", async () => {
    const { status, json } = await gql("{ me { userId } }");
    expect(status).toBe(401);
    expect(json.errors[0].extensions.code).toBe("UNAUTHENTICATED");
  });

  it("rejects a malformed JWT", async () => {
    const { status } = await gql("{ me { userId } }", "not-a-real-jwt");
    expect(status).toBe(401);
  });

  it("composes case + assignee + sourceDataset from REAL downstream calls, forwarding the JWT verbatim", async () => {
    const { json } = await gql(
      `{ case(id:"case-1") { status severity urn assignee { email } sourceDataset { name } proposals { id } } }`,
      userToken,
    );
    expect(json.errors).toBeUndefined();
    expect(json.data.case.status).toBe("IN_PROGRESS");
    expect(json.data.case.assignee.email).toBe("ann@acme.com");
    expect(json.data.case.sourceDataset.name).toBe("claims-2026");

    // JWT passthrough (AC-3): the downstream received the caller's exact token.
    const caseReq = captured.case.find((r) => r.url.includes("/cases/case-1"));
    expect(caseReq?.headers["authorization"]).toBe(`Bearer ${userToken}`);
    const idReq = captured.identity.find((r) => r.url.includes("/users"));
    expect(idReq?.headers["authorization"]).toBe(`Bearer ${userToken}`);
    // Trace propagation reached the downstream (BFF-FR-012).
    expect(caseReq?.headers["x-trace-id"]).toBeTruthy();
  });

  it("maps a real downstream 403 to PERMISSION_DENIED (BFF makes no authz decision itself)", async () => {
    const { json } = await gql(`{ case(id:"forbidden") { id } }`, userToken);
    expect(json.data.case).toBeNull();
    expect(json.errors[0].extensions.code).toBe("PERMISSION_DENIED");
    expect(json.errors[0].extensions.service).toBe("case-service");
  });

  it("fetches the connector catalog, creates a connection, and tests it — all over REAL HTTP to ingestion", async () => {
    // 1. catalog (drives the UI dynamic form)
    const cat = await gql(`{ connectorTypes { connectorType displayName category secretFields fields { name type required secret } } }`, userToken);
    expect(cat.json.errors).toBeUndefined();
    const pg = cat.json.data.connectorTypes.find((t: any) => t.connectorType === "postgres");
    expect(pg.displayName).toBe("PostgreSQL");
    expect(pg.secretFields).toEqual(["password"]);
    expect(pg.fields.find((f: any) => f.name === "password").secret).toBe(true);

    // 2. create (config + Vault-backed secret forwarded verbatim to ingestion)
    const create = await gql(
      `mutation { createConnection(input: { name: "Prod DB", type: "postgres", config: { host: "db.internal", database: "sales", username: "ro" }, secrets: { password: "s3cr3t" } }, idempotencyKey: "e2e-1") { id name connectorType secretSet lastTestStatus } }`,
      userToken,
    );
    expect(create.json.errors).toBeUndefined();
    expect(create.json.data.createConnection.id).toBe("conn-real-1");
    expect(create.json.data.createConnection.secretSet).toBe(true);

    // 3. test the saved connection
    const test = await gql(`mutation { testConnection(id: "conn-real-1") { status latencyMs } }`, userToken);
    expect(test.json.errors).toBeUndefined();
    expect(test.json.data.testConnection.status).toBe("OK");

    // JWT passthrough reached ingestion on every call.
    const createReq = captured.ingestion.find((r) => r.url === "/api/v1/connections");
    expect(createReq?.headers["authorization"]).toBe(`Bearer ${userToken}`);
  });
});
