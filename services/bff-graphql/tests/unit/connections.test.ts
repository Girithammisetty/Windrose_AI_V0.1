import { describe, it, expect } from "vitest";
import { makeApolloServer } from "../../src/server.js";
import { ErrorCode } from "../../src/errors/errors.js";
import { makeTestContext, testConfig } from "../helpers/context.js";
import { mockFetch, type CapturedRequest } from "../helpers/mockFetch.js";

const cfg = testConfig();

/** ingestion-service double: connector catalog + connection CRUD + test-connection. */
function ingestion() {
  return mockFetch((req: CapturedRequest) => {
    if (req.path === "/api/v1/connector-types" && req.method === "GET") {
      return {
        status: 200,
        body: {
          data: [
            {
              connector_type: "postgres",
              display_name: "PostgreSQL",
              category: "database",
              fields: [
                { name: "host", type: "string", required: true, secret: false, default: null, enum: null, help: "Hostname" },
                { name: "port", type: "integer", required: false, secret: false, default: 5432, enum: null, help: null },
                { name: "ssl_mode", type: "enum", required: false, secret: false, default: "require", enum: ["disable", "require"], help: null },
                { name: "password", type: "string", required: false, secret: true, default: null, enum: null, help: "pw" },
              ],
              secret_fields: ["password"],
              config_schema: { additionalProperties: false },
            },
          ],
        },
      };
    }
    // list
    if (req.path === "/api/v1/connections" && req.method === "GET") {
      return {
        status: 200,
        body: {
          data: [
            { id: "conn-1", name: "Prod Warehouse", connector_type: "postgres", config: { host: "db" }, secrets: { password: "•••" }, secret_set: true, last_test_status: "ok", last_tested_at: "2026-07-10T00:00:00Z" },
          ],
          page: { next_cursor: "c2", has_more: true },
        },
      };
    }
    // single
    if (req.path === "/api/v1/connections/conn-1" && req.method === "GET") {
      return { status: 200, body: { data: { id: "conn-1", name: "Prod Warehouse", connector_type: "postgres", config: { host: "db" }, secrets: { password: "•••" }, secret_set: true } } };
    }
    if (req.path === "/api/v1/connections/missing" && req.method === "GET") {
      return { status: 404, body: { error: { code: "NOT_FOUND", message: "no connection", trace_id: "t404" } } };
    }
    // create
    if (req.path === "/api/v1/connections" && req.method === "POST") {
      return { status: 201, body: { data: { id: "conn-new", name: req.body.name, connector_type: req.body.connector_type, config: req.body.config, secrets: { password: "•••" }, secret_set: true, last_test_status: "ok" } } };
    }
    // test-connection: saved
    if (req.path === "/api/v1/connections/conn-1/test" && req.method === "POST") {
      return { status: 200, body: { data: { status: "ok", latency_ms: 12, error_category: null, error_detail: null } } };
    }
    // test-connection: adhoc
    if (req.path === "/api/v1/connections:test" && req.method === "POST") {
      const ok = req.body?.secrets?.password === "correct";
      return {
        status: 200,
        body: ok
          ? { data: { status: "ok", latency_ms: 8 } }
          : { data: { status: "failed", latency_ms: 5, error_category: "AUTH_FAILED", error_detail: "authentication failed (scrubbed)" } },
      };
    }
    // delete
    if (req.path === "/api/v1/connections/conn-1" && req.method === "DELETE") {
      return { status: 204 };
    }
    return { status: 404, body: { error: { code: "NOT_FOUND", message: "x", trace_id: "t" } } };
  });
}

describe("connection resolvers (ingestion passthrough, JWT forwarded)", () => {
  it("reshapes the connector-type catalog (snake_case → camelCase, secret flags)", async () => {
    const server = makeApolloServer(cfg);
    const { fetchImpl, requests } = ingestion();
    const ctx = await makeTestContext(fetchImpl);

    const res = await server.executeOperation(
      { query: `{ connectorTypes { connectorType displayName category secretFields fields { name type required secret default enum help } } }` },
      { contextValue: ctx },
    );
    const body = res.body.kind === "single" ? res.body.singleResult : null;
    expect(body?.errors).toBeUndefined();
    const types: any[] = (body?.data as any)?.connectorTypes ?? [];
    expect(types).toHaveLength(1);
    const pg = types[0];
    expect(pg.connectorType).toBe("postgres");
    expect(pg.displayName).toBe("PostgreSQL");
    expect(pg.category).toBe("database");
    expect(pg.secretFields).toEqual(["password"]);
    const byName = Object.fromEntries(pg.fields.map((f: any) => [f.name, f]));
    expect(byName.host.required).toBe(true);
    expect(byName.port.default).toBe(5432);
    expect(byName.ssl_mode.enum).toEqual(["disable", "require"]);
    expect(byName.password.secret).toBe(true);
    // JWT forwarded verbatim to ingestion.
    expect(requests[0]?.headers["authorization"]).toMatch(/^Bearer /);
  });

  it("lists connections cursor-paginated and never leaks secret values", async () => {
    const server = makeApolloServer(cfg);
    const { fetchImpl } = ingestion();
    const ctx = await makeTestContext(fetchImpl);
    const res = await server.executeOperation(
      { query: `{ connections(first: 10) { nodes { id name connectorType secretSet secretFields lastTestStatus urn } pageInfo { nextCursor hasMore } } }` },
      { contextValue: ctx },
    );
    const body = res.body.kind === "single" ? res.body.singleResult : null;
    expect(body?.errors).toBeUndefined();
    const conn = (body?.data as any).connections;
    expect(conn.nodes[0].id).toBe("conn-1");
    expect(conn.nodes[0].secretSet).toBe(true);
    expect(conn.nodes[0].secretFields).toEqual(["password"]);
    expect(conn.nodes[0].urn).toBe("wr:t-42:ingestion:connection/conn-1");
    expect(conn.pageInfo).toEqual({ nextCursor: "c2", hasMore: true });
    // Response never carries a raw credential value.
    expect(JSON.stringify(body?.data)).not.toContain("s3cr3t");
  });

  it("returns null for a connection masked/absent (404 → null, no error)", async () => {
    const server = makeApolloServer(cfg);
    const { fetchImpl } = ingestion();
    const ctx = await makeTestContext(fetchImpl);
    const res = await server.executeOperation({ query: `{ connection(id:"missing") { id } }` }, { contextValue: ctx });
    const body = res.body.kind === "single" ? res.body.singleResult : null;
    expect(body?.errors).toBeUndefined();
    expect((body?.data as any).connection).toBeNull();
  });

  it("creates a connection, forwarding config + secrets + idempotency key", async () => {
    const server = makeApolloServer(cfg);
    const { fetchImpl, requests } = ingestion();
    const ctx = await makeTestContext(fetchImpl);
    const res = await server.executeOperation(
      {
        query: `mutation($input: CreateConnectionInput!, $k: String!) {
          createConnection(input: $input, idempotencyKey: $k) { id name connectorType secretSet lastTestStatus }
        }`,
        variables: {
          input: { name: "New DB", type: "postgres", config: { host: "db", database: "d", username: "u" }, secrets: { password: "correct" } },
          k: "idem-1",
        },
      },
      { contextValue: ctx },
    );
    const body = res.body.kind === "single" ? res.body.singleResult : null;
    expect(body?.errors).toBeUndefined();
    expect((body?.data as any).createConnection.id).toBe("conn-new");
    const post = requests.find((r) => r.method === "POST" && r.path === "/api/v1/connections");
    expect(post?.body.connector_type).toBe("postgres");
    expect(post?.body.secrets.password).toBe("correct");
    expect(post?.headers["idempotency-key"]).toBe("idem-1");
  });

  it("test-connection (adhoc) surfaces OK vs AUTH_FAILED verbatim", async () => {
    const server = makeApolloServer(cfg);
    const { fetchImpl } = ingestion();
    const ctx = await makeTestContext(fetchImpl);

    const ok = await server.executeOperation(
      { query: `mutation($c: JSON!, $s: JSON!) { testConnection(type:"postgres", config:$c, secrets:$s) { status latencyMs errorCategory } }`, variables: { c: { host: "db" }, s: { password: "correct" } } },
      { contextValue: ctx },
    );
    const okBody = ok.body.kind === "single" ? ok.body.singleResult : null;
    expect((okBody?.data as any).testConnection).toEqual({ status: "OK", latencyMs: 8, errorCategory: null });

    const bad = await server.executeOperation(
      { query: `mutation($c: JSON!, $s: JSON!) { testConnection(type:"postgres", config:$c, secrets:$s) { status errorCategory errorDetail } }`, variables: { c: { host: "db" }, s: { password: "nope" } } },
      { contextValue: ctx },
    );
    const badBody = bad.body.kind === "single" ? bad.body.singleResult : null;
    expect((badBody?.data as any).testConnection.status).toBe("FAILED");
    expect((badBody?.data as any).testConnection.errorCategory).toBe("AUTH_FAILED");
  });

  it("test-connection (saved by id) probes the saved connection endpoint", async () => {
    const server = makeApolloServer(cfg);
    const { fetchImpl, requests } = ingestion();
    const ctx = await makeTestContext(fetchImpl);
    const res = await server.executeOperation(
      { query: `mutation { testConnection(id:"conn-1") { status latencyMs } }` },
      { contextValue: ctx },
    );
    const body = res.body.kind === "single" ? res.body.singleResult : null;
    expect((body?.data as any).testConnection).toEqual({ status: "OK", latencyMs: 12 });
    expect(requests.some((r) => r.path === "/api/v1/connections/conn-1/test" && r.method === "POST")).toBe(true);
  });

  it("testConnection without id or type is a local VALIDATION_FAILED (no downstream call)", async () => {
    const server = makeApolloServer(cfg);
    const { fetchImpl, requests } = ingestion();
    const ctx = await makeTestContext(fetchImpl);
    const res = await server.executeOperation({ query: `mutation { testConnection { status } }` }, { contextValue: ctx });
    const body = res.body.kind === "single" ? res.body.singleResult : null;
    expect(body?.errors?.[0]?.extensions?.code).toBe(ErrorCode.VALIDATION_FAILED);
    expect(requests.length).toBe(0);
  });

  it("deletes a connection (204 → true)", async () => {
    const server = makeApolloServer(cfg);
    const { fetchImpl, requests } = ingestion();
    const ctx = await makeTestContext(fetchImpl);
    const res = await server.executeOperation({ query: `mutation { deleteConnection(id:"conn-1") }` }, { contextValue: ctx });
    const body = res.body.kind === "single" ? res.body.singleResult : null;
    expect((body?.data as any).deleteConnection).toBe(true);
    expect(requests.some((r) => r.path === "/api/v1/connections/conn-1" && r.method === "DELETE")).toBe(true);
  });

  it("maps a failed pre-persist probe (ingestion 424) to CONNECTION_TEST_FAILED with detail", async () => {
    const server = makeApolloServer(cfg);
    const { fetchImpl } = mockFetch((req: CapturedRequest) => {
      if (req.path === "/api/v1/connections" && req.method === "POST") {
        return { status: 424, body: { error: { code: "CONNECTION_TEST_FAILED", message: "connection test failed", details: { error_category: "AUTH_FAILED" }, trace_id: "tr-424" } } };
      }
      return { status: 404, body: { error: { code: "NOT_FOUND", message: "x", trace_id: "t" } } };
    });
    const ctx = await makeTestContext(fetchImpl);
    const res = await server.executeOperation(
      {
        query: `mutation($input: CreateConnectionInput!, $k: String!) { createConnection(input:$input, idempotencyKey:$k) { id } }`,
        variables: { input: { name: "bad", type: "postgres", config: { host: "db" }, secrets: { password: "wrong" } }, k: "k1" },
      },
      { contextValue: ctx },
    );
    const body = res.body.kind === "single" ? res.body.singleResult : null;
    expect(body?.errors?.[0]?.extensions?.code).toBe(ErrorCode.CONNECTION_TEST_FAILED);
    expect((body?.errors?.[0]?.extensions?.details as any)?.error_category).toBe("AUTH_FAILED");
    expect(body?.errors?.[0]?.extensions?.traceId).toBe("tr-424");
  });
});
