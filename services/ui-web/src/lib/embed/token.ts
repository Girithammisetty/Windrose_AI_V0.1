import "server-only";
import { timingSafeEqual } from "node:crypto";

/** Surfaces a tenant may embed. The token's `surface` claim is a subset. */
export const KNOWN_SURFACES = new Set(["dashboard", "cases", "copilot"]);
export const MAX_TTL = 3600; // 1h ceiling for an embed token
export const DEFAULT_TTL = 600; // 10 min

/** Constant-time comparison of the presented embed secret against the
 * configured one. Returns false when no secret is configured (fail closed).
 * PRODUCTION: this becomes a per-tenant secret lookup in identity-service. */
export function secretOk(provided: string | null): boolean {
  const expected = process.env.WINDROSE_EMBED_SECRET;
  if (!expected || !provided) return false;
  const a = Buffer.from(provided);
  const b = Buffer.from(expected);
  return a.length === b.length && timingSafeEqual(a, b);
}

export interface EmbedBody {
  tenantId?: string;
  workspaceId?: string;
  sub?: string;
  scopes?: string[];
  surface?: string[];
  resourceId?: string;
  ttlSeconds?: number;
}

export interface ResolvedEmbed {
  mint: {
    sub: string;
    tenantId: string;
    workspaceId: string;
    scopes: string[];
    surface: string[];
    ttlSeconds: number;
  };
  surface: string[];
  ttl: number;
  path: string;
}

/** Pure validation + resolution of an embed request into mint params + the
 * embed URL path. The governance (surface allowlist, TTL clamp, required
 * fields) is here — unit-testable without RSA keygen. */
export function resolveEmbedRequest(
  body: EmbedBody,
): { error: string; status: number } | ResolvedEmbed {
  if (!body.tenantId || !body.workspaceId || !body.sub) {
    return { error: "tenantId, workspaceId and sub are required", status: 400 };
  }
  const surface = (body.surface ?? ["dashboard"]).filter((s) => KNOWN_SURFACES.has(s));
  if (surface.length === 0) {
    return { error: `surface must be one of ${[...KNOWN_SURFACES].join(", ")}`, status: 400 };
  }
  const ttl = Math.min(Math.max(body.ttlSeconds ?? DEFAULT_TTL, 60), MAX_TTL);
  const primary = surface[0];
  const path =
    primary === "dashboard" && body.resourceId
      ? `/embed/dashboard/${encodeURIComponent(body.resourceId)}`
      : `/embed/${primary}`;
  return {
    mint: {
      sub: body.sub,
      tenantId: body.tenantId,
      workspaceId: body.workspaceId,
      scopes: body.scopes && body.scopes.length ? body.scopes : ["chart.dashboard.read"],
      surface,
      ttlSeconds: ttl,
    },
    surface,
    ttl,
    path,
  };
}
