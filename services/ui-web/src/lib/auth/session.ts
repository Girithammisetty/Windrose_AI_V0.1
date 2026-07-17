/**
 * Session helpers shared by the auth/graphql/rt route handlers. The user JWT
 * lives in an httpOnly cookie (never readable by JS); these read it server-side.
 */
import "server-only";
import { cookies } from "next/headers";
import { decodeJwt } from "jose";

export const SESSION_COOKIE = "wr_session";
/** Embed session cookie (SameSite=None; Secure; Partitioned) for the headless
 * `/embed/*` surfaces framed on a tenant's origin. Kept separate from the main
 * first-party session so third-party-cookie behavior never affects normal use. */
export const EMBED_COOKIE = "wr_embed";

export interface SessionClaims {
  sub: string;
  tenantId: string;
  workspaceId: string;
  scopes: string[];
  type: string;
  exp?: number;
}

export async function getSessionToken(): Promise<string | null> {
  const store = await cookies();
  // First-party session wins; fall back to the embed session so the shared
  // /api/graphql data path authenticates unchanged inside an iframe.
  return store.get(SESSION_COOKIE)?.value ?? store.get(EMBED_COOKIE)?.value ?? null;
}

export function parseClaims(token: string): SessionClaims | null {
  try {
    const c = decodeJwt(token);
    return {
      sub: String(c.sub ?? ""),
      tenantId: String((c as Record<string, unknown>).tenant_id ?? ""),
      workspaceId: String((c as Record<string, unknown>).workspace_id ?? ""),
      scopes: Array.isArray((c as Record<string, unknown>).scopes)
        ? ((c as Record<string, unknown>).scopes as string[])
        : [],
      type: String((c as Record<string, unknown>).typ ?? "user"),
      exp: typeof c.exp === "number" ? c.exp : undefined,
    };
  } catch {
    return null;
  }
}

export async function getSessionClaims(): Promise<SessionClaims | null> {
  const token = await getSessionToken();
  if (!token) return null;
  const claims = parseClaims(token);
  if (!claims) return null;
  if (claims.exp && claims.exp * 1000 < Date.now()) return null;
  return claims;
}
