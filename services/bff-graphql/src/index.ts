/**
 * bff-graphql entrypoint.
 *
 * A minimal Node http server (no extra web framework) that serves:
 *   POST/GET /graphql  -> Apollo Server 4
 *   GET /healthz       -> liveness (no deps)
 *   GET /readyz        -> readiness
 *   GET /metrics       -> Prometheus text (minimal)
 * Edge JWT verification runs before the GraphQL request executes; a missing or
 * invalid token is rejected with 401 UNAUTHENTICATED (BFF-FR-010).
 */
import http from "node:http";
import { HeaderMap } from "@apollo/server";
import { loadConfig } from "./config.js";
import { makeApolloServer } from "./server.js";
import { buildContext, type IncomingHeaders } from "./context.js";
import { makeJwks } from "./auth/jwt.js";
import { loadManifest } from "./plugins/persistedQueries.js";
import { GraphQLError } from "graphql";

/** Max accepted request body. This BFF only ingests GraphQL operations and
 * persisted-query hashes — kilobytes, not megabytes. The cap makes an oversized
 * or streaming POST a fast 413 instead of an unbounded heap buffer + double-copy
 * through JSON.parse (OOM DoS lever). */
const MAX_BODY_BYTES = 4 * 1024 * 1024;

class PayloadTooLargeError extends Error {}

async function readBody(req: http.IncomingMessage, maxBytes = MAX_BODY_BYTES): Promise<string> {
  const chunks: Buffer[] = [];
  let total = 0;
  for await (const c of req) {
    const buf = c as Buffer;
    total += buf.length;
    if (total > maxBytes) {
      // Stop reading and tear the socket down so we don't drain a huge upload.
      req.destroy();
      throw new PayloadTooLargeError();
    }
    chunks.push(buf);
  }
  return Buffer.concat(chunks).toString("utf8");
}

function headerVal(v: string | string[] | undefined): string | undefined {
  return Array.isArray(v) ? v[0] : v;
}

export async function main(): Promise<http.Server> {
  // TODO(observability): env-gated OpenTelemetry tracing — DEFERRED.
  // The Go/Python services trace via go-common/otelx + py-common/otelx, both
  // gated on WINDROSE_OTEL_ENABLED / OTEL_EXPORTER_OTLP_ENDPOINT (clean no-op
  // when unset, never crash on an unreachable collector). Mirroring that here
  // would tie the UI→backend boundary into distributed traces: bootstrap a
  // NodeSDK BEFORE this line (it must patch http before the module graph loads,
  // so it belongs in a `--require`d preload, not inline), auto-instrument the
  // node:http server + outbound fetch to the domain services, propagate the
  // incoming `traceparent` (already read in buildContext), and export OTLP to
  // OTEL_EXPORTER_OTLP_ENDPOINT only when WINDROSE_OTEL_ENABLED is truthy — a
  // no-op otherwise, with the exporter configured to fail silently so a down
  // collector never destabilizes the BFF.
  // NOT wired: this needs new deps not in package.json (@opentelemetry/sdk-node,
  // instrumentation-http, instrumentation-graphql, exporter-trace-otlp-grpc/http)
  // plus a preload entrypoint. Adding them half-way risks the request hot path,
  // so tracing is intentionally left out until those deps + a preload land.
  const cfg = loadConfig();
  const manifest = loadManifest(); // hash->document artifact (empty by default)
  const jwks = makeJwks(cfg);
  const apollo = makeApolloServer(cfg, { manifest });
  await apollo.start();

  const server = http.createServer(async (req, res) => {
    const url = new URL(req.url ?? "/", "http://localhost");
    const path = url.pathname;

    if (req.method === "GET" && path === "/healthz") {
      return json(res, 200, { status: "ok" });
    }
    if (req.method === "GET" && path === "/readyz") {
      return json(res, 200, { status: "ready" });
    }
    if (req.method === "GET" && path === "/metrics") {
      res.writeHead(200, { "content-type": "text/plain" });
      return res.end(`# bff-graphql\nbff_up 1\n`);
    }
    if (path !== "/graphql") {
      return json(res, 404, { error: { code: "NOT_FOUND", message: "no such route" } });
    }

    // Reject oversized bodies fast: a declared Content-Length over the cap, or
    // an actual stream that exceeds it mid-read (readBody throws).
    const declaredLen = Number(headerVal(req.headers["content-length"]) ?? "0");
    if (declaredLen > MAX_BODY_BYTES) {
      return json(res, 413, { errors: [{ message: "Request body too large" }] });
    }
    let bodyText = "";
    if (req.method === "POST") {
      try {
        bodyText = await readBody(req);
      } catch (e) {
        if (e instanceof PayloadTooLargeError) {
          return json(res, 413, { errors: [{ message: "Request body too large" }] });
        }
        throw e;
      }
    }
    const incoming: IncomingHeaders = {
      authorization: headerVal(req.headers["authorization"]),
      traceparent: headerVal(req.headers["traceparent"]),
      "x-trace-id": headerVal(req.headers["x-trace-id"]),
    };

    // Edge auth first: fail fast on a missing/invalid JWT (BFF-FR-010).
    let ctx;
    try {
      ctx = await buildContext({ config: cfg, jwks }, incoming);
    } catch (e) {
      const err = e instanceof GraphQLError ? e : new GraphQLError("Unauthenticated");
      return json(res, 401, { errors: [{ message: err.message, extensions: err.extensions }] });
    }

    const headers = new HeaderMap();
    for (const [k, v] of Object.entries(req.headers)) {
      if (typeof v === "string") headers.set(k, v);
      else if (Array.isArray(v)) headers.set(k, v.join(","));
    }

    let parsedBody: unknown;
    try {
      parsedBody = bodyText ? JSON.parse(bodyText) : {};
    } catch {
      return json(res, 400, { errors: [{ message: "Invalid JSON in request body" }] });
    }

    const httpResp = await apollo.executeHTTPGraphQLRequest({
      httpGraphQLRequest: {
        method: req.method ?? "POST",
        headers,
        search: url.search ?? "",
        body: parsedBody,
      },
      context: async () => ctx,
    });

    res.statusCode = httpResp.status ?? 200;
    for (const [k, v] of httpResp.headers) res.setHeader(k, v);
    if (httpResp.body.kind === "complete") {
      res.end(httpResp.body.string);
    } else {
      for await (const chunk of httpResp.body.asyncIterator) res.write(chunk);
      res.end();
    }
  });

  await new Promise<void>((resolve) => server.listen(cfg.port, resolve));
   
  console.log(`bff-graphql listening on :${cfg.port} (mode=${cfg.mode}, introspection=${cfg.introspection})`);
  return server;
}

function json(res: http.ServerResponse, status: number, body: unknown): void {
  res.writeHead(status, { "content-type": "application/json" });
  res.end(JSON.stringify(body));
}

// Run when invoked directly.
if (import.meta.url === `file://${process.argv[1]}`) {
  main().catch((e) => {
     
    console.error("fatal", e);
    process.exit(1);
  });
}
