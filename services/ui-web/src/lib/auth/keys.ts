/**
 * Server-only signing material for the DEV auth path.
 *
 * In production, AUTH_MODE=oidc and login is delegated to Keycloak (code+PKCE);
 * this module is never used to mint tokens. In dev/e2e (AUTH_MODE=dev) the app
 * mints REAL RS256 user JWTs and publishes a REAL JWKS at /api/auth/jwks, so a
 * locally-booted bff-graphql can verify signatures against it exactly as it
 * would against identity-service. No fake tokens: real crypto, real JWKS,
 * real edge verification downstream.
 */
import "server-only";
import { generateKeyPair, exportJWK, importJWK, SignJWT, type JWK, type KeyLike } from "jose";

interface Material {
  privateKey: KeyLike;
  publicJwk: JWK;
  kid: string;
}

let cached: Promise<Material> | null = null;

async function build(): Promise<Material> {
  const kid = "ui-web-dev-1";
  const envPriv = process.env.DEV_JWT_PRIVATE_JWK;
  const envPub = process.env.DEV_JWT_PUBLIC_JWK;
  if (envPriv && envPub) {
    const priv = JSON.parse(envPriv) as JWK;
    const pub = JSON.parse(envPub) as JWK;
    const privateKey = (await importJWK(priv, "RS256")) as KeyLike;
    return { privateKey, publicJwk: { ...pub, kid: pub.kid ?? kid, alg: "RS256", use: "sig" }, kid: pub.kid ?? kid };
  }
  // Ephemeral keypair for a self-contained `pnpm dev`.
  const { privateKey, publicKey } = await generateKeyPair("RS256", { extractable: true });
  const publicJwk = { ...(await exportJWK(publicKey)), kid, alg: "RS256", use: "sig" };
  return { privateKey, publicJwk, kid };
}

function material(): Promise<Material> {
  if (!cached) cached = build();
  return cached;
}

export async function jwks(): Promise<{ keys: JWK[] }> {
  const { publicJwk } = await material();
  return { keys: [publicJwk] };
}

export interface DevClaims {
  sub: string;
  tenantId: string;
  scopes: string[];
  workspaceId: string;
  /** Embed tokens set this + a `surface` allowlist + a short TTL. The token is
   * otherwise a normal user JWT (aud=windrose) so every downstream service
   * still accepts it; the embed route enforces `surface`. */
  embed?: boolean;
  surface?: string[];
  /** Override the default 8h lifetime (seconds). Embed tokens are short. */
  ttlSeconds?: number;
}

export async function mintUserToken(claims: DevClaims): Promise<string> {
  const { privateKey, kid } = await material();
  const payload: Record<string, unknown> = {
    tenant_id: claims.tenantId,
    typ: "user",
    scopes: claims.scopes,
    workspace_id: claims.workspaceId,
  };
  if (claims.embed) {
    payload.embed = true;
    payload.surface = claims.surface ?? [];
  }
  const jwt = new SignJWT(payload)
    .setProtectedHeader({ alg: "RS256", kid })
    .setSubject(claims.sub)
    .setIssuedAt()
    .setIssuer(process.env.JWT_ISSUER ?? "windrose-dev")
    .setExpirationTime(
      claims.ttlSeconds ? Math.floor(Date.now() / 1000) + claims.ttlSeconds : "8h",
    );
  // When wired to the real stack (make up), JWT_AUDIENCE is set so every
  // downstream service's aud check (aud=windrose) accepts the minted token.
  // Left unset for the self-contained UI e2e (no aud enforcement there).
  if (process.env.JWT_AUDIENCE) jwt.setAudience(process.env.JWT_AUDIENCE);
  return jwt.sign(privateKey);
}
