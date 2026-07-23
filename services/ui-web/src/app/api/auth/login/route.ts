/**
 * Dev login (AUTH_MODE=dev): posts credentials, mints a REAL RS256 user JWT, and
 * stores it in an httpOnly cookie. In prod this route is disabled — login is the
 * OIDC code+PKCE flow against Keycloak (UI-FR-004). The minted token is verified
 * for real by the BFF against /api/auth/jwks.
 */
import { NextRequest, NextResponse } from "next/server";
import { mintUserToken } from "@/lib/auth/keys";
import { resolveLogin } from "@/lib/auth/personas";
import { SESSION_COOKIE } from "@/lib/auth/session";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

// Scopes granted to a dev user — broad, since the DOWNSTREAM services enforce
// real authz. The UI never gates on these except to render permission states.
const DEV_SCOPES = [
  "case.case.read",
  "case.case.write",
  "dataset.dataset.read",
  "experiment.experiment.read",
  "chart.dashboard.read",
  "usage.report.read",
  "agent.proposal.read",
  "agent.proposal.decide",
];

export async function POST(req: NextRequest) {
  // Fail CLOSED: dev login requires an explicit AUTH_MODE=dev opt-in, and can
  // never run in a production build regardless of AUTH_MODE — a deployment
  // that forgets to set AUTH_MODE must not silently expose a password-less
  // login (this previously defaulted to "dev" when the env var was unset).
  if (process.env.AUTH_MODE !== "dev" || process.env.NODE_ENV === "production") {
    return NextResponse.json(
      { error: "Dev login disabled; use OIDC." },
      { status: 403 },
    );
  }

  const { email, tenantId, workspaceId } = (await req.json().catch(() => ({}))) as {
    email?: string;
    tenantId?: string;
    workspaceId?: string;
  };
  if (!email) {
    return NextResponse.json({ error: "email required" }, { status: 400 });
  }

  // `make up` injects DATACERN_PERSONAS: a JSON map of persona email ->
  // {sub, tenantId, workspaceId, scopes} bound to the REAL provisioned tenant +
  // workspace and the projection grants seeded for that persona. When present it
  // is AUTHORITATIVE: an unknown email is rejected (403) rather than silently
  // minted into a ghost tenant. The t-acme/ws-claims dev defaults apply only
  // when no personas map is configured at all (self-contained ui dev).
  const resolution = resolveLogin(email, process.env.DATACERN_PERSONAS);
  if (resolution.kind === "unknown-user") {
    return NextResponse.json({ error: "unknown user" }, { status: 403 });
  }
  const p = resolution.kind === "persona" ? resolution.persona : {};

  const sub = p.sub || `user-${email.split("@")[0]}`;
  const token = await mintUserToken({
    sub,
    tenantId: tenantId || p.tenantId || "t-acme",
    workspaceId: workspaceId || p.workspaceId || "ws-claims",
    scopes: p.scopes && p.scopes.length ? p.scopes : DEV_SCOPES,
    platformAdmin: p.platformAdmin === true,
  });

  const res = NextResponse.json({ ok: true, email });
  res.cookies.set(SESSION_COOKIE, token, {
    httpOnly: true,
    sameSite: "lax",
    // The guard above already 403s in production, so TS narrows NODE_ENV and
    // flags this comparison as always-false (TS2367). Keep the defensive
    // check anyway (cast defeats the narrowing) so `secure` stays correct if
    // the guard ever changes.
    secure: (process.env.NODE_ENV as string) === "production",
    path: "/",
    maxAge: 60 * 60 * 8,
  });
  return res;
}
