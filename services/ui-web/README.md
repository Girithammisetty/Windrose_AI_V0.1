# ui-web

The single web application for the Windrose insurance-claims platform (BRD 22):
data management, ML, dashboards, case triage, admin, and the cross-cutting
**agentic surfaces** (copilot, approval inbox, trace visualizer, AI cost panel,
EU AI Act Article 50 labels).

Next.js 15 (App Router) · React 19 · TypeScript strict · TanStack Query v5 (server
state) · Zustand (UI state only) · Radix + Tailwind (token theming) · TanStack
Table + Virtual · React Hook Form + Zod · Vitest + Playwright.

All request/response data flows through **bff-graphql** (one same-origin proxy
route forwards the user's Bearer JWT); all live status flows over **direct SSE to
realtime-hub**. There is **no fake data in the runtime path** — components render
real BFF query results (mocks live only in tests).

## Run

```bash
export PATH="/opt/homebrew/opt/node@20/bin:/opt/homebrew/bin:$PATH"   # node@20 is keg-only
pnpm install
pnpm dev            # http://localhost:3100 (needs a bff-graphql at BFF_URL)
pnpm typecheck      # strict tsc --noEmit
pnpm lint           # eslint (bans setInterval polling — UI-FR-012)
pnpm test           # Vitest unit/component
pnpm build          # production build (per-route budgets)
pnpm e2e:install && pnpm e2e   # Playwright against a REAL booted bff-graphql
```

### Configuration (env)

| Var | Purpose |
|---|---|
| `BFF_URL` | bff-graphql GraphQL endpoint the server proxy forwards to |
| `REALTIME_HUB_URL` | realtime-hub base (server mints SSE tickets against it) |
| `NEXT_PUBLIC_REALTIME_HUB_URL` | hub base surfaced to the browser for EventSource |
| `AGENT_RUNTIME_URL` | agent-runtime base for copilot chat |
| `AUTH_MODE` | `dev` (mint local RS256 JWT + publish JWKS) or `oidc` (Keycloak) |
| `DEV_JWT_PRIVATE_JWK` / `DEV_JWT_PUBLIC_JWK` | fixed dev signing key (optional; ephemeral otherwise) |

### Auth

Dev: the login form posts to `/api/auth/login`, which mints a **real RS256 user
JWT** into an httpOnly cookie and publishes the matching JWKS at
`/api/auth/jwks` — a locally-booted bff-graphql verifies signatures against it
exactly as against identity-service. Prod: OIDC code+PKCE against Keycloak. The
UI attaches the JWT as `Bearer` on every BFF call via the same-origin proxy; it
makes **zero** authz decisions and renders `PERMISSION_DENIED` states from GraphQL.

## Route / screen map (which BFF operation backs each)

| Route | Screen | BFF query/mutation |
|---|---|---|
| `/` | Welcome (tiles, pending approvals, cost snapshot) | `proposalsInbox`, `workspaceCostPanel` |
| `/data` · `/data/datasets/[id]` | Datasets list + detail (profile/lineage/query tabs) | `datasets`, `dataset` (+`profile`) |
| `/ml` · `/ml/experiments/[id]` · `/ml/runs/[id]` | Experiments, runs (live), run inspector | `experiments`, `experiment`(+runs), `run` |
| `/dashboards` · `/dashboards/[id]` | Dashboard list + chart grid w/ provenance | `dashboards`, `dashboard`(+charts+data+provenance) |
| `/cases` · `/cases/[id]` | Virtualized triage list + case detail + disposition | `caseSearch`, `case`, `updateCase` |
| `/inbox` | Approval inbox (diff, decisions, bulk) | `proposalsInbox`, `decideProposal` |
| `/copilot` · `/copilot/runs/[id]` | Full copilot, agent-run trace visualizer | copilot chat (agent-runtime), `agentRun` |
| `/admin/*` | Users/tenant/usage/… | `workspaceCostPanel` (usage); others pending BFF ops |

Screens whose BFF operation does not exist in the current schema (connections,
ingestions, upload, admin lists, audit, archive) render a consistent "not yet
wired" panel naming the required operation — never fake data (BR-15).

## Agentic surfaces

- **Copilot drawer** on every page (`CopilotDrawer`): context = current resource
  URN (AC-3), real SSE token streaming, citations, suggested actions that route
  to the **proposal** flow only (BR-13), persistent non-suppressible AI label.
- **Approval inbox** (`/inbox`): JSON-aware `DiffView`, approve/reject(reason
  required)/edit-args/respond, and **bulk-approve that excludes destructive/
  high-risk proposals by construction** (`lib/agentic/proposals.ts`, AC-5).
- **Trace visualizer** (`TraceVisualizer`): virtualized tool-call tree, error
  nodes auto-expanded, span deep links (AC-7).
- **AI cost panel** (`CostPanel`): budget bars with 80/95/100 thresholds, live via
  `usage.events.v1` (no polling).
- **AI labels & provenance** (`AiLabel`, `ProvenanceBadge`): non-suppressible
  Article 50 disclosure + AI-generated badge wherever `provenance` is non-null.

## Global UX guarantees

Virtualized `DataTable` everywhere (< 100 DOM rows over 1M-row sets); cursor
pagination; **SSE-driven status, zero polling** (ESLint bans `setInterval`);
optimistic updates with rollback; skeleton/error/empty triads with `trace_id`;
per-route code splitting; WCAG 2.1 AA (keyboard, focus, ARIA, contrast, reduced
motion); token-based dark mode; i18n message catalog.

## Testing — what ran live

- **Vitest** (48 tests): diff engine, destructive bulk-exclusion, EventBridge
  patchers, trace flattening, SSE degradation state machine, AI-label
  non-suppressibility, reject-reason gate, DataTable virtualization.
- **Playwright e2e** boots the **REAL bff-graphql** (`services/bff-graphql`, real
  Apollo + schema + RS256 edge JWT verification against this app's JWKS) pointed
  at an OpenAPI-shaped contract server that also speaks the **real realtime-hub
  SSE wire protocol** (`tests-e2e/contract-server.mjs`). The real Next app drives:
  login → view a claim case → open the copilot drawer (AI label + context URN) →
  stream a response over real SSE → see the proposal in the inbox → **approve it
  via the real `decideProposal` mutation** → destructive proposal excluded from
  bulk; plus reject-reason-required and fail-closed route guard. The one
  concession vs. booting all 20 domain services is that downstream REST is the
  contract server — the BFF client path calling it is 100% real (the fallback the
  build task explicitly permits).
