/**
 * PLATFORM-ADMIN guide — the "one guide for platform admin" from the brief.
 * Audience is "admin" (shown when useCapabilities().isAdmin). Covers tenant/use-case
 * setup, installing packs, users & RBAC, SSO, secrets, observability, agent
 * governance, embedding, and audit export — grounded in the real admin surfaces.
 */
import type { HelpArticle } from "./types";

export const ADMIN_ARTICLES: HelpArticle[] = [
  {
    slug: "admin-overview",
    title: "Platform admin: overview",
    summary: "What an admin sets up and governs — the map of the Admin area.",
    area: "admin",
    audience: "admin",
    order: 1,
    related: ["admin-tenant-usecase", "admin-packs", "admin-users-rbac"],
    body: `
As a **workspace/tenant admin** you set up the environment your teams work in and
govern how it behaves. The **Admin** section in the sidebar is your home base.

Your responsibilities, roughly in order:

1. **Tenant & use cases** — create workspaces (use cases) and switch between them.
2. **Install a pack** — drop in a full vertical solution (roles, dashboards, case
   taxonomy, decision tables, agents) as one governed bundle.
3. **Users & roles** — invite people and grant the pack's personas.
4. **Sign-in (SSO)** — connect your identity provider.
5. **Secrets & infra** — point the platform at your secret store and observability.
6. **AI governance** — set operator ceilings and manage custom agents.
7. **Embedding & audit** — white-label a surface and stream audit events to your SIEM.

Each is covered in its own article below. Everything an admin does is itself
audited.
`,
  },
  {
    slug: "admin-tenant-usecase",
    title: "Tenants and use cases (workspaces)",
    summary: "How the tenant / workspace / group model works and how to create a use case.",
    area: "admin",
    audience: "admin",
    order: 2,
    related: ["admin-users-rbac", "admin-overview"],
    body: `
Windrose's isolation model has three layers:

- **Tenant** — the hard security wall. All data is row-level isolated by tenant;
  nothing crosses it.
- **Workspace (use case)** — a data partition inside a tenant. Most work is scoped
  to a use case; a tenant can run several.
- **Group** — an optional way to organize members and grant access.

## Create / switch a use case

1. Use the **use-case switcher** (top-left of the top bar).
2. **New use case** creates a workspace and switches you into it (your session is
   re-minted for the new scope).
3. Switch back at any time from the same dropdown.

> Access is additive across two rails (workspace membership + group grants) — a
> member sees a use case only if they're granted into it.
`,
  },
  {
    slug: "admin-packs",
    title: "Installing and removing capability packs",
    summary: "Install a full vertical solution as one governed, reversible bundle.",
    area: "admin",
    audience: "admin",
    order: 3,
    related: ["admin-users-rbac", "admin-overview"],
    body: `
A **capability pack** is an entire vertical solution — datasets, semantic models,
dashboards, case dispositions, decision tables, roles, and AI agents — installed
as one bundle. This is what gives a fresh tenant its personas and surfaces.

## Install

1. Open **Packs** in the sidebar to see the catalog.
2. Open a pack for its manifest: exactly what it will create, and any **deferred**
   components (things Core can't materialize yet — shown honestly, never faked).
3. **Dry-run plan** first — it previews every change with **no side effects**.
4. **Install into this workspace.** Components are materialized **as you** (your
   permissions), and an **origin-tagged ledger** records everything created.
5. If a pack's dashboards depend on a semantic model that needs approval, the
   install pauses at **awaiting approval**; a steward approves the model, then you
   click **Complete install**.

## Remove

- **Uninstall** reverses exactly what the pack created (reversible objects are
  deleted; anything Core has no revert verb for is tombstoned and reported). The
  ledger is what makes the reversal precise.

> One pack usually powers one tenant; a shared **library** pack (e.g. an
> investigation framework) can layer underneath a vertical pack.
`,
  },
  {
    slug: "admin-users-rbac",
    title: "Users, roles, and RBAC",
    summary: "Invite people, grant pack personas, and understand the closed permission model.",
    area: "admin",
    audience: "admin",
    order: 4,
    related: ["admin-packs", "admin-agents"],
    body: `
People get access by being **members** of a use case and holding one or more
**roles**. A pack ships its personas as roles (e.g. Intake Analyst, Investigator,
Operations Manager, Auditor), each a bundle of fine-grained **capabilities**.

## Grant access

1. In **Admin → Users** (or Roles), invite a user into the workspace.
2. Assign one or more **roles** — the pack's personas are the roles you'll pick
   from.
3. The user's sidebar and available actions are shaped by their roles: they only
   see what their capabilities allow.

## The model, briefly

- Permissions are a **closed set of verbs** (read, list, create, update, delete,
  execute, assign, approve, admin, export, share) over resources — packs compose
  these, they never invent new verbs.
- The **Admin** role passes every check (it's the wildcard).
- The UI gating is **fail-safe** — a capability the client can't confirm hides the
  feature — but the services enforce for real regardless.

> The single most important capability to place carefully is **approve** — give it
> to your operations-manager / reviewer persona, since that's who disposes
> proposals under four-eyes.
`,
  },
  {
    slug: "admin-sso",
    title: "Single sign-on (per-tenant OIDC)",
    summary: "Connect your own identity provider so users log in with your IdP.",
    area: "admin",
    audience: "admin",
    order: 5,
    related: ["admin-overview", "admin-embedding"],
    body: `
Each tenant can **bring its own identity provider**. Users then sign in with your
corporate IdP instead of local credentials, and identities are federated per
tenant.

## Connect an IdP

1. In **Admin**, open the **SSO / Identity** card.
2. Register your **OIDC** provider — issuer URL and client details.
3. Sign-in then routes by issuer to your IdP; existing local login continues to
   work as a fallback during migration.

Once configured, the login flow uses standard OIDC (with PKCE) against your
provider, and each tenant's users authenticate against that tenant's IdP.

> This is a self-service, per-tenant setting — one tenant's IdP choice never
> affects another's.
`,
  },
  {
    slug: "admin-secrets-infra",
    title: "Secrets and infrastructure (bring your own)",
    summary: "Point the platform at your secret store; observability and cloud are configurable.",
    area: "admin",
    audience: "admin",
    order: 6,
    related: ["admin-observability", "admin-overview"],
    body: `
Windrose is **bring-your-own-infra** friendly. Secrets, observability, and cloud
targets are configuration, not code.

## Secrets backend

- The platform reads connection secrets and keys through a pluggable **secrets
  store**, selectable per deployment: **HashiCorp Vault**, **AWS Secrets Manager**,
  **Azure Key Vault**, or **GCP Secret Manager**. Set the backend and the services
  fetch secrets from it — no plaintext secrets in the app.

## Cloud & deploy

- The production deployment ships as CI/CD + Helm + Terraform for **AWS / GCP /
  Azure**, with credentials externalized — so you run it in your account.

> These are set by whoever operates the deployment; as a tenant admin you mostly
> confirm the store is connected and healthy.
`,
  },
  {
    slug: "admin-observability",
    title: "Observability and health",
    summary: "Tracing and RED metrics across the services; how to see what's healthy.",
    area: "admin",
    audience: "admin",
    order: 7,
    related: ["admin-secrets-infra", "admin-audit"],
    body: `
Every service emits **distributed traces** and **RED metrics** (rate, errors,
duration), env-gated so you can wire them to your stack.

## What you get

- **Tracing** across the ~20 services, so a request can be followed end-to-end.
- **RED metrics** per service for dashboards and alerting.
- **Health / readiness** endpoints per service — the platform surfaces degraded
  dependencies **loudly** (e.g. a readiness check that reports a downstream store
  is down) rather than failing silently.

> If a capability suddenly errors for everyone, the readiness signals are the first
> place to look — a crashed dependency (search index, warehouse) shows up there.
`,
  },
  {
    slug: "admin-agents",
    title: "AI governance: operator ceilings and custom agents",
    summary: "Cap what agents can do platform-wide, and build no-code custom agents.",
    area: "admin",
    audience: "admin",
    order: 8,
    related: ["copilot", "admin-users-rbac"],
    body: `
You govern the AI, not just the people. Two controls matter most.

## Operator ceilings

- In **Admin**, the **operator ceilings** console sets platform-wide caps — the
  maximum token budget and data-scope any agent may use. These **clamp every
  agent**, including tenant-authored custom ones, so no agent can exceed the
  ceiling you set.

## Custom agents (no-code)

1. From the agents admin, choose **New custom agent**.
2. Pick the **role** it acts as, the **tools** it may call, the **workspace** it's
   scoped to, and its **guardrails**: a **data scope** (what it can read), a
   **token budget** (per run), and **PII-egress redaction**.
3. Save — it runs on the shared, governed agent runtime and, like every agent,
   **proposes** changes for human approval and **cannot self-approve**.

- You can also **auto-bind** persona copilots to your pack's roles so each persona
  has an assistant out of the box.

> Guardrails are validated and clamped at author time to your ceilings — an agent
> can never be configured above the platform maximum.
`,
  },
  {
    slug: "admin-embedding",
    title: "Embedded UI (white-label)",
    summary: "Embed a Windrose surface in your own app, per-user and per-tenant.",
    area: "admin",
    audience: "admin",
    order: 9,
    related: ["admin-sso", "admin-overview"],
    body: `
You can **embed** a Windrose surface (e.g. a dashboard) inside your own
application, white-labeled and scoped to a tenant.

## Set it up

1. In **Admin**, open **Embed config**: set the **allowed origins** (which sites
   may frame the surface) and rotate the embed secret when needed.
2. Your app exchanges a token for an **embed session** and loads the headless
   surface in an iframe; frame-ancestors are restricted to your allowed origins.
3. For per-user embedding, you can federate the user's identity from your tenant's
   **OIDC** id token — no shared secret required.

> Embedding respects the same tenant isolation and permissions as the full app.
`,
  },
  {
    slug: "admin-audit",
    title: "Audit and SIEM export",
    summary: "Every action is audited; stream those events to your SIEM.",
    area: "admin",
    audience: "admin",
    order: 10,
    related: ["admin-observability", "admin-overview"],
    body: `
Everything of consequence is **audited** into a tamper-evident, hash-chained,
write-once store — who did what, to which record, when, and the outcome.

## Stream to your SIEM

- Audit events are additionally published on a stable **export topic** so you can
  forward them to your SIEM. An optional **webhook forwarder** delivers them (with
  retries and circuit-breaking) to a Splunk-HEC-style endpoint.
- The export is **additive** and happens **after** the durable audit write — it
  never weakens the audit record.

## Good to know

- The export depends on the audit store being up; if the store is down, the audit
  write (and therefore the export) pauses until it recovers — by design, so you
  never get an export without a matching audit record.

> Use this to satisfy central logging / compliance requirements without giving
> anyone direct access to the audit store.
`,
  },
];
