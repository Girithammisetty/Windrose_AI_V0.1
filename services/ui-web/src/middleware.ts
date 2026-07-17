import { NextRequest, NextResponse } from "next/server";
import { decodeJwt } from "jose";
import { EMBED_COOKIE, SESSION_COOKIE } from "@/lib/auth/session";

/** The embed surface a path belongs to, for the token `surface` allowlist. */
function surfaceOf(pathname: string): string | null {
  if (pathname.startsWith("/embed/dashboard")) return "dashboard";
  if (pathname.startsWith("/embed/cases")) return "cases";
  if (pathname.startsWith("/embed/copilot")) return "copilot";
  return null;
}

/**
 * Route guard (UI-FR-004): unauthenticated users are redirected to /login for
 * any app route. Fail-closed. Per-permission authz is enforced downstream; the
 * UI only renders permission states from GraphQL PERMISSION_DENIED.
 *
 * `/embed/*` is the headless embedding surface: it is framable by the tenant's
 * allowed origins (frame-ancestors), authenticates from the short-lived embed
 * token (consumed once from `?t=` into the partitioned `wr_embed` cookie), and
 * is NEVER redirected to the interactive login.
 */
export function middleware(req: NextRequest) {
  const { pathname } = req.nextUrl;

  if (pathname.startsWith("/embed")) {
    const token = req.nextUrl.searchParams.get("t") ?? req.cookies.get(EMBED_COOKIE)?.value;
    const surface = surfaceOf(pathname);

    // Per-tenant frame-ancestors: prefer the value bound into the (signed)
    // embed token by identity-service; fall back to the deploy-wide env only in
    // dev / when no token carries them. Never '*'.
    let ancestorsFromToken: string | null = null;
    if (surface && token) {
      try {
        const claims = decodeJwt(token) as { surface?: string[]; frame_ancestors?: string[] };
        // Surface allowlist (defense in depth; scopes+RBAC are the real boundary
        // at the BFF): a token may only render the surfaces it was minted for.
        const allowed = Array.isArray(claims.surface) ? claims.surface : [];
        if (allowed.length > 0 && !allowed.includes(surface)) {
          return new NextResponse("This embed is not permitted for this surface.", {
            status: 403,
          });
        }
        if (Array.isArray(claims.frame_ancestors) && claims.frame_ancestors.length > 0) {
          ancestorsFromToken = claims.frame_ancestors.join(" ");
        }
      } catch {
        // Unparseable token — let the data path fail closed (401) downstream.
      }
    }

    const res = NextResponse.next();
    if (req.nextUrl.searchParams.get("t")) {
      // Third-party-iframe-safe: SameSite=None + Secure + Partitioned (CHIPS).
      // localhost is a secure context so Secure cookies work in dev.
      res.cookies.set(EMBED_COOKIE, req.nextUrl.searchParams.get("t")!, {
        httpOnly: true,
        sameSite: "none",
        secure: true,
        partitioned: true,
        path: "/",
        maxAge: 3600,
      });
    }
    // Allow framing ONLY by the tenant's configured origins (never '*').
    const ancestors =
      ancestorsFromToken || process.env.WINDROSE_EMBED_ANCESTORS || "'self'";
    res.headers.set("Content-Security-Policy", `frame-ancestors ${ancestors}`);
    return res;
  }

  const hasSession = req.cookies.has(SESSION_COOKIE);
  if (!hasSession) {
    const url = req.nextUrl.clone();
    // The bare root is the front door: send signed-out visitors to the
    // marketing page; deep links still go to login with a return path.
    if (pathname === "/") {
      url.pathname = "/welcome";
      return NextResponse.redirect(url);
    }
    url.pathname = "/login";
    url.searchParams.set("next", pathname);
    return NextResponse.redirect(url);
  }
  return NextResponse.next();
}

export const config = {
  // Guard everything except login, API routes, and static assets.
  matcher: [
    "/((?!login|welcome|api|_next/static|_next/image|favicon.ico|icon.svg|windrose-embed.js).*)",
  ],
};
