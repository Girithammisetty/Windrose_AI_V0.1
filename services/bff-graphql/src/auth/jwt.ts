/**
 * Edge JWT verification (BFF-FR-010).
 *
 * The BFF verifies the inbound user JWT's signature/exp/iss/aud against the
 * identity-service JWKS **only to fail fast** — it then forwards the ORIGINAL
 * token verbatim to every downstream call. The BFF makes NO authorization
 * decision here: authz lives entirely in the domain services (a downstream 403
 * becomes PERMISSION_DENIED). `tenant_id` is read from the verified token only
 * for log correlation; it is never used as a query argument.
 */
import { createRemoteJWKSet, jwtVerify, decodeJwt, type JWTPayload } from "jose";
import type { Config } from "../config.js";
import { gqlError, ErrorCode } from "../errors/errors.js";

/** Master-BRD JWT claims (MASTER-FR-011). */
export interface Claims extends JWTPayload {
  sub?: string;
  tenant_id?: string;
  // Present on user tokens; the workspace the caller is acting in. Used by
  // workspace-scoped writes (e.g. createPipeline) that don't take it as an arg.
  workspace_id?: string;
  typ?: "user" | "service" | "agent_obo" | "agent_autonomous";
  agent_id?: string;
  agent_version?: string;
  obo_sub?: string;
  scopes?: string[];
  /** First-class cross-tenant platform operator (identity-service). */
  platform_admin?: boolean;
}

export interface VerifiedIdentity {
  /** The raw bearer token, forwarded verbatim downstream. */
  token: string;
  claims: Claims;
  /** true when the signature was cryptographically verified at the edge. */
  verified: boolean;
}

export type JwksResolver = ReturnType<typeof createRemoteJWKSet>;

/** Build a cached remote JWKS resolver (JWKS refresh handled by jose, <=5min). */
export function makeJwks(cfg: Config): JwksResolver | undefined {
  if (!cfg.verifyJwt) return undefined;
  return createRemoteJWKSet(new URL(cfg.jwksUrl), {
    cacheMaxAge: 5 * 60 * 1000,
    cooldownDuration: 30 * 1000,
  });
}

export function extractBearer(authorization: string | undefined): string | undefined {
  if (!authorization) return undefined;
  const m = /^Bearer\s+(.+)$/i.exec(authorization.trim());
  return m?.[1];
}

/**
 * Verify the inbound Authorization header. Throws UNAUTHENTICATED on a
 * missing/invalid/expired token. `alg=none` is rejected (jose enforces the
 * JWKS key algs; MASTER-FR-014).
 */
export async function verifyInbound(
  authorization: string | undefined,
  cfg: Config,
  jwks: JwksResolver | undefined,
): Promise<VerifiedIdentity> {
  const token = extractBearer(authorization);
  if (!token) {
    throw gqlError(ErrorCode.UNAUTHENTICATED, "Missing or malformed Authorization header");
  }

  if (!cfg.verifyJwt || !jwks) {
    // Dev/test edge-verification disabled: decode (no signature check) for
    // context/logging; the token is still forwarded verbatim downstream.
    let claims: Claims;
    try {
      claims = decodeJwt(token) as Claims;
    } catch {
      throw gqlError(ErrorCode.UNAUTHENTICATED, "Malformed JWT");
    }
    return { token, claims, verified: false };
  }

  try {
    const { payload } = await jwtVerify(token, jwks, {
      issuer: cfg.jwtIssuer,
      audience: cfg.jwtAudience,
    });
    return { token, claims: payload as Claims, verified: true };
  } catch (e) {
    throw gqlError(ErrorCode.UNAUTHENTICATED, `Invalid token: ${(e as Error).message}`);
  }
}
