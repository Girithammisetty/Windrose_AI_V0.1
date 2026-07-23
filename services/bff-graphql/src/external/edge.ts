/**
 * BRD 60 WS3 — public governed edge for external agents.
 *
 * bff-graphql is the ONE publicly-exposed ingress; the internal services
 * (agent-runtime, the mcp-gateway) are not reachable from outside. This module
 * exposes the external agent's two verbs at that public edge and forwards them
 * to the internal ingress, carrying the caller's own external-agent token:
 *
 *   POST /external/v1/intents  -> agent-runtime  POST /external/v1/intents
 *   POST /external/v1/mcp       -> mcp-gateway    POST /mcp   (JSON-RPC list/call)
 *
 * It is a THIN proxy: the edge verifies the token's signature and that it is a
 * live AGENT principal (fail-fast at the boundary), then forwards verbatim. The
 * internal ingress re-authorizes and applies the full four-eyes / tier-ceiling /
 * guardrail pipeline unchanged — the edge adds no trust and removes none. A
 * write `tools/call` therefore still comes back `PROPOSAL_REQUIRED`, and a
 * propose still lands as a governed pending proposal in the WORM chain.
 */
import type http from "node:http";
import type { Config } from "../config.js";
import { verifyInbound, type Claims, type JwksResolver } from "../auth/jwt.js";

const EXTERNAL_INTENTS = "/external/v1/intents";
const EXTERNAL_MCP = "/external/v1/mcp";

export interface EdgeDeps {
  cfg: Config;
  jwks: JwksResolver | undefined;
  /** Injectable for unit tests; defaults to the global fetch at call time. */
  fetchImpl?: typeof fetch;
}

/** True when `path` is one of the external governed-edge routes. */
export function isExternalEdgePath(path: string): boolean {
  return path === EXTERNAL_INTENTS || path === EXTERNAL_MCP;
}

function isAgentTyp(typ: unknown): boolean {
  return typ === "agent_autonomous" || typ === "agent_obo";
}

function headerVal(v: string | string[] | undefined): string | undefined {
  return Array.isArray(v) ? v[0] : v;
}

function sendJson(res: http.ServerResponse, status: number, body: unknown): void {
  res.writeHead(status, { "content-type": "application/json" });
  res.end(JSON.stringify(body));
}

/**
 * Handle one external-edge request. `bodyText` is the already-read POST body.
 * Resolves after the response has been written.
 */
export async function handleExternalEdge(
  req: http.IncomingMessage,
  res: http.ServerResponse,
  path: string,
  bodyText: string,
  deps: EdgeDeps,
): Promise<void> {
  if (req.method !== "POST") {
    return sendJson(res, 405, { error: { code: "METHOD_NOT_ALLOWED", message: "POST only" } });
  }

  const authorization = headerVal(req.headers["authorization"]);

  // Edge auth: verify signature (fail-fast on junk) and confirm this is an
  // agent principal — the external edge is for a customer's own agent, not for
  // a user or a service token.
  let claims: Claims;
  try {
    ({ claims } = await verifyInbound(authorization, deps.cfg, deps.jwks));
  } catch (e) {
    const message = e instanceof Error ? e.message : "Unauthenticated";
    return sendJson(res, 401, { error: { code: "UNAUTHENTICATED", message } });
  }
  if (!isAgentTyp(claims.typ)) {
    return sendJson(res, 403, {
      error: { code: "FORBIDDEN", message: "the external governed edge requires an agent token" },
    });
  }

  const target =
    path === EXTERNAL_INTENTS
      ? `${deps.cfg.services.agentRuntime}${EXTERNAL_INTENTS}`
      : `${deps.cfg.services.mcpGateway}/mcp`;

  const fwdHeaders: Record<string, string> = {
    "content-type": "application/json",
    authorization: authorization as string, // present — verifyInbound would have thrown otherwise
  };
  const traceparent = headerVal(req.headers["traceparent"]);
  if (traceparent) fwdHeaders["traceparent"] = traceparent;
  const traceId = headerVal(req.headers["x-trace-id"]);
  if (traceId) fwdHeaders["x-trace-id"] = traceId;

  const f = deps.fetchImpl ?? fetch;
  let upstream: Response;
  try {
    upstream = await f(target, { method: "POST", headers: fwdHeaders, body: bodyText || "{}" });
  } catch (e) {
    const message = e instanceof Error ? e.message : "upstream request failed";
    return sendJson(res, 502, { error: { code: "UPSTREAM_UNAVAILABLE", message } });
  }

  // Copy the upstream status + body back verbatim so the SDK sees the internal
  // ingress's real envelope (PROPOSAL_REQUIRED, GUARDRAIL_VIOLATION, the pending
  // proposal view, etc.) — the edge never rewrites the governed decision.
  const text = await upstream.text();
  res.writeHead(upstream.status, {
    "content-type": upstream.headers.get("content-type") ?? "application/json",
  });
  res.end(text);
}
