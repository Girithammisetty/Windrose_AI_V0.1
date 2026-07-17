/**
 * Per-request GraphQL context.
 *
 * Assembles the verified identity, the real per-request downstream clients
 * (each carrying the forwarded JWT + trace headers) and the dataloaders. The
 * context is where "one request = one JWT, forwarded everywhere" is realised.
 */
import { randomUUID } from "node:crypto";
import type { Config } from "./config.js";
import { buildClients, type Clients } from "./clients/index.js";
import { buildLoaders, type Loaders } from "./loaders/index.js";
import { verifyInbound, type JwksResolver, type VerifiedIdentity } from "./auth/jwt.js";
import type { FetchImpl } from "./clients/base.js";

export interface GraphQLContext {
  config: Config;
  identity: VerifiedIdentity;
  clients: Clients;
  loaders: Loaders;
  traceId: string;
}

export interface IncomingHeaders {
  authorization?: string;
  traceparent?: string;
  "x-trace-id"?: string;
}

export interface BuildContextDeps {
  config: Config;
  jwks: JwksResolver | undefined;
  fetchImpl?: FetchImpl;
}

export async function buildContext(
  deps: BuildContextDeps,
  headers: IncomingHeaders,
): Promise<GraphQLContext> {
  const { config, jwks, fetchImpl } = deps;

  // Edge verification (fail-fast). Throws UNAUTHENTICATED on bad tokens.
  const identity = await verifyInbound(headers.authorization, config, jwks);

  const traceId = headers["x-trace-id"] ?? deriveTraceId(headers.traceparent) ?? randomUUID();

  const clientCtx = {
    authorization: headers.authorization, // forwarded verbatim (BFF-FR-010)
    traceparent: headers.traceparent,
    traceId,
  };

  const clients = buildClients(config, clientCtx, fetchImpl);
  const loaders = buildLoaders(clients);

  return { config, identity, clients, loaders, traceId };
}

function deriveTraceId(traceparent?: string): string | undefined {
  // W3C traceparent: version-traceid-spanid-flags
  if (!traceparent) return undefined;
  const parts = traceparent.split("-");
  return parts.length >= 2 ? parts[1] : undefined;
}
