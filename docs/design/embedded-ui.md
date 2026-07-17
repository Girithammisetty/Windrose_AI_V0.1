# Design — Embedded UI (iframe / white-label embedding)

## Problem

A tenant wants a Windrose surface (a dashboard, a case queue, the copilot)
**inside their own app** — iframe or white-label — without their users doing a
second interactive login, and without exposing Windrose chrome.

## Why it's tractable

Auth is **token-centric end to end**: ui-web reads a user JWT from its session
cookie and forwards it as a Bearer to the BFF; the BFF verifies it against
identity-service JWKS (RS256, iss/aud); every downstream service trusts the same
JWT and enforces RBAC/OPA/audit off it. **So if a valid, tightly-scoped JWT
reaches the embedded context, everything downstream already works.** The work is
the embedding envelope, not the auth core.

## Blockers today (confirmed in code)

1. Session cookie is `SameSite=Lax` → not sent inside a cross-site iframe.
2. No `frame-ancestors`/CSP → embedding neither sanctioned nor scoped.
3. The `(app)` layout always renders full chrome (`AppShell`).
4. Auth is interactive (dev-login / OIDC redirect) — can't silently auth in an
   iframe; no signed-embed-token path.

## The extension

Standard embedded-analytics pattern (Looker/Metabase-style):

1. **Embed-token exchange (embedding server).** The tenant's *backend* calls a
   Windrose embed endpoint with a shared **embed secret** + the user context
   `{tenantId, workspaceId, sub, scopes/role, surface, ttl}` and gets back a
   short-lived, scoped JWT + an embed URL. The secret never reaches the browser.
   - The minted token is a **normal user JWT** (`aud=windrose` so every
     downstream service still accepts it) carrying extra claims `embed:true` +
     `surface:[...]` and a **short TTL** (~10 min) and **narrow scopes +
     workspace**. Least privilege; the embed route enforces `surface`.
2. **Headless embed routes** under `/embed/*` with a **bare layout** (no
   `AppShell`): `/embed/dashboard/[id]` renders just the dashboard. Theme param
   to match the host.
3. **Cross-site cookie**: the embed page receives the token via `?t=` on first
   load and stores it in a dedicated cookie `wr_embed`
   (`SameSite=None; Secure; Partitioned` — CHIPS), separate from the main
   session. `getSessionToken()` falls back to `wr_embed`, so the existing
   `/api/graphql` data path authenticates unchanged.
4. **`frame-ancestors` CSP**: middleware sets
   `Content-Security-Policy: frame-ancestors <tenant's allowed origins>` for
   `/embed/*` (no `*`), and does NOT redirect `/embed/*` to login.
5. **Embed SDK** (`windrose-embed.js`): `Windrose.embed(el, {token, surface,
   theme})` injects the iframe + wires `postMessage` for auto-resize / nav /
   auth-refresh (host origin validated).

## Governance

An embed token is just another user JWT with **tighter** scope: short TTL,
workspace+role+surface-bound, minted server-side behind a shared secret. Every
embedded action passes the same RBAC/OPA/audit as the first-party UI. No new
privilege path, no `frame-ancestors: *`.

## Status — Increment 1 (this slice) — BUILT + e2e-VERIFIED (2026-07-15)

`mintUserToken` extended with `embed`/`surface`/`ttl` (aud stays windrose);
ui-web embedding-server route `POST /api/embed/token` (shared-secret gated,
constant-time; pure `resolveEmbedRequest` for surface allowlist + TTL
clamp[60s..1h]); `wr_embed` `SameSite=None;Secure;Partitioned` cookie set by
middleware from `?t=` + `getSessionToken` fallback; headless
`/embed/dashboard/[id]` (root layout, no AppShell); middleware
`frame-ancestors` CSP for `/embed/*` + no login-redirect. **7 route unit tests**
+ full UI suite **386 green**. **LIVE e2e**: minted an embed token (wrong
secret → 401), served a customer host page on a DIFFERENT origin
(`localhost:8899`) that iframes the Payer KPI dashboard — all 4 charts render
real data with **no Windrose chrome and no interactive login**; the embed route
returns `Content-Security-Policy: frame-ancestors http://localhost:8899 'self'`.

## Increment 2 — BUILT + e2e-VERIFIED (2026-07-15)

Cases + copilot headless surfaces (`/embed/cases`, `/embed/copilot`), a shared
`useEmbedFrame` hook (theme param + postMessage `ready`/`resize`, host-origin-
validated inbound theme changes), middleware **surface allowlist** enforcement
(token `surface` claim vs path → 403 cross-surface), and the embed SDK
`public/windrose-embed.js` (`Windrose.embed(el,{embedUrl,theme})` → inject
iframe + wire resize/theme, validate frame origin). **386 UI tests green.**
LIVE: SDK-embedded cases queue on `localhost:8899` renders in **dark theme** (6
real cases, no chrome, auto-resized); copilot surface renders headless with its
AI disclosure; a cases-scoped token on `/embed/dashboard` → **403** (surface
gate).

## Production hardening — BUILT + e2e-VERIFIED (2026-07-15)

- **identity-service `POST /token/embed`** (`internal/domain/token_embed.go`):
  per-tenant secret validation (sha256, constant-time), scoped short-TTL mint
  via `Issuer.IssueWithTTL` carrying `workspace_id`/`embed`/`surface`/
  `frame_ancestors`. Claims + `wireClaims` + `TokenIssuer` extended (additive,
  omitempty — zero regression to existing tokens). `tenant_embed_configs` table
  (migration `0004`) + Store methods (memory + postgres). Admin
  `PUT /tenants/{id}/embed-config` generates + returns the secret once and sets
  the allowed origins (tenant-admin scoped). **2 acceptance tests + full
  identity suite green.**
- **ui-web** `/api/embed/token` **proxies to identity** when `IDENTITY_URL` is
  set (falls back to local harness mint in dev). Middleware sets
  `frame-ancestors` from the **signed token's `frame_ancestors` claim**
  (per-tenant), env only as fallback.
- **LIVE e2e**: tenant admin sets a per-tenant secret via identity → ui-web
  proxies the mint → token carries `frame_ancestors=[localhost:8899,'self']` →
  the embed route emits `Content-Security-Policy: frame-ancestors
  http://localhost:8899 'self'` (per-tenant, from the token) and the iframe
  frames successfully; wrong per-tenant secret → 401.

**Honest dev-stack caveat:** the full proxy-path *data* render is blocked only
in the local harness — identity-service's LocalSigner "keys do not survive
restarts" and its restart-generated key isn't in the shared dev JWKS aggregator
(:8300) the BFF trusts, so an identity-minted token 401s at the BFF *in dev*.
The render itself is fully proven via the dev-mint path (all 3 surfaces). In
production identity is the token authority (persistent Vault-backed key
published to the JWKS the BFF verifies), so identity-minted embed tokens verify
everywhere. Deferred: OIDC-federated embed SSO; a UI screen for the embed-config
admin (API is done).
