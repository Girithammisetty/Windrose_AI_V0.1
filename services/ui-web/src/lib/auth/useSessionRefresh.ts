"use client";
import { useEffect } from "react";

/** The Datacern session JWT is short-lived (5 min, MASTER-FR-010). Poll
 * /api/auth/refresh well inside that window so a real-OIDC session renews
 * silently instead of the user hitting a raw "session expired" error mid-
 * task. A no-op (cheap, harmless) for dev-login sessions, which have no
 * refresh cookie and an 8h token to begin with — see the route's own comment.
 * A failed refresh (IdP refresh token expired/revoked) just stops silently;
 * the next real API call surfaces the existing expired-session UX. */
const REFRESH_INTERVAL_MS = 2 * 60 * 1000;

export function useSessionRefresh(): void {
  useEffect(() => {
    // UI-FR-012 bans DATA polling (SSE patches query caches instead); this
    // timer is auth-cookie renewal, which no SSE channel can perform
    // (HttpOnly cookie rotation requires a same-origin HTTP response).
    // eslint-disable-next-line no-restricted-syntax
    const id = setInterval(() => {
      fetch("/api/auth/refresh", { method: "POST" }).catch(() => {});
    }, REFRESH_INTERVAL_MS);
    return () => clearInterval(id);
  }, []);
}
